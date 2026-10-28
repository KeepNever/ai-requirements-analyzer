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
from pipeline import scenes as scene_engine  # noqa: E402
from pipeline.service_diagnosis import (  # noqa: E402
    SD_PROMPT_VERSION, analyze_service_diagnosis,
    build_citations_service, build_service_report)

MIN_MATERIAL_CHARS = 30


def run_scene_case(material_path: str, scene: str = "service_diagnosis",
                   mock: bool = True, project_name: str | None = None) -> Path:
    """跑一次场景分析并落盘到版本化存储，返回 run 目录。"""
    if scene != "presale" and scene not in scene_engine.list_template_scenes():
        raise ValueError(f"未知场景：{scene}（可用模板场景：{sorted(scene_engine.list_template_scenes())}）")
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
    try:
        doc = storage.add_document(pid, material_path.name,
                                   material_path.read_text(encoding="utf-8"))
        doc_id = doc["document"]["document_id"]
    except ValueError as e:
        if "重复" not in str(e):
            raise
        # 同内容文档已存在：复用其当前版本（重跑同一材料不新建）
        tree = storage.get_project_tree(pid)
        doc_id = tree["documents"][0]["document_id"]
    refs = [{"document_id": doc_id, "version_id": "v1"}]

    # 3. 运行（meta 带 scene 标识——场景隔离的锚点）
    prompt_ver = (scene_engine.get_scene(scene).get("prompt_version", "v1.0")
                  if scene != "presale" else "v1.7.1")
    rdir = storage.create_run(pid, refs, prompt_ver, "deepseek-chat")
    meta = storage._read_json(rdir / "meta.json")
    meta["scene"] = scene
    storage._write_json(rdir / "meta.json", meta)

    if scene == "presale":
        # 售前场景：复用 workflow 四节点组件（Prompt/校验/评分不动），产物入 V2 存储
        import json as _json
        from pipeline import workflow as wf, prompts, validators, scoring
        from pipeline import llm_client
        material_block = wf._fence(data.cleaned_text())
        log = []
        node1 = wf._llm_with_gates("提取", prompts.LLM_NODE1.format(
            rules=prompts._COMMON_RULES, material=material_block),
            validators.validate_node1, data, log) if not mock else \
            wf._load_mock_fixture(Path(material_path).stem).get("node1", {"explicit_needs": [], "competitor_signals": []})
        node2 = wf._llm_with_gates("分析", prompts.LLM_NODE2.format(
            capability=prompts.CAPABILITY_LIST, rules=prompts._COMMON_RULES,
            material=material_block, node1_json=_json.dumps(node1, ensure_ascii=False)),
            validators.validate_node2, data, log) if not mock else \
            _json.dumps and {"potential_needs": [], "pending_confirmations": [],
                             "capability_match": [], "priority_dimensions": []}
        priority = scoring.build_priority_table(node2["priority_dimensions"], node1["explicit_needs"])
        analysis = {"node1": node1, "node2": node2, "priority": priority,
                    "meta": {"model": "deepseek-chat", "weights": scoring.WEIGHTS_VERSION, "mock": mock}}
        report_md = wf._render_report(pname, data, node1, node2, priority, log)
        cits = storage.build_citations(analysis, pid, refs)
        storage.save_run_outputs(rdir, analysis, report_md, cits,
                                 None if mock else llm_client.usage_summary())
        return rdir

    # 模板场景（JSON 配置驱动，Task 8 场景化）
    sc = scene_engine.get_scene(scene)
    sd = scene_engine.analyze_scene(sc, data.cleaned_text(), mock=mock)
    cits = scene_engine.build_citations_scene(sd, sc, pid, refs)
    report_md = scene_engine.render_report_scene(sd, sc, pname, rdir.name)
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
