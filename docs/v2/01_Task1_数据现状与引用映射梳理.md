# 01 · Task 1 交付：数据现状与引用映射梳理

> V2 主线 Task 1 ｜ 日期：2026-10-20 ｜ 性质：只梳理不改行为（28 测试保持全绿）
> 上游方案：`00_产品方案与迭代路线图_v1.0.md` §8.3 存储实体、§14 Task 1

---

## 一、现有数据流全图

```text
材料文件（.md，P1/P2 编号段落）
  │ cleaner.load_material()      截取「## 原始材料」之后内容
  ▼
cleaner.clean(raw)               纯代码节点
  │
  ├─▶ CleanResult.segments       核心中间结构（内存中，仅 mapping.json 落盘）
  │     [{pid, did, text, raw_text, pii_scrubbed}]
  │       pid: 原始编号 P1…      did: 清洗编号 D001…
  │       text: 脱敏后文本（进 LLM）  raw_text: 脱敏前原文（留痕）
  │
  ├─▶ mapping.json               {mapping:[{pid,did}], stats:{输入块数,解析段落数,PII脱敏段落数,刷屏合并数}}
  │
  ├─▶ cleaned_text()             "D001 xxx\n\nD002 yyy…" ← 进 LLM 的材料形态
  ▼
workflow.run_case()
  ├─ 节点2 提取 → node1           {customer_background[], business_goals[],
  │                                explicit_needs[{id,content,dids[],quote}],
  │                                competitor_signals[]}
  ├─ 节点3~7 分析 → node2         {potential_needs[], pending_confirmations[],
  │                                capability_match[{target,verdict,reason}],
  │                                priority_dimensions[{id,impact,urgency,coverage,signal,reason}]}
  ├─ scoring.build_priority_table → priority[]
  │                                [{id,content,impact,urgency,coverage,signal,reason,weighted,tier}]
  └─ 节点8 落盘
        analysis.json             {node1, node2, priority, meta}
        report.md                 人类可读报告（F1~F9）
        mapping.json              （见上）
        usage.json                成本记录
```

## 二、引用字段现状（对照方案 §7.3 引用卡片字段）

| 方案要求字段 | 现状 | 差距 |
|---|---|---|
| source_paragraph_id（原文段落 P 编号） | ❌ 引用里只有 dids（D 编号），P↔D 对照在 mapping.json 可换算 | 需在引用结构中直接携带或统一换算 |
| clean_paragraph_id（D 编号） | ✅ explicit_needs[].dids[]；priority_dimensions[].reason 文内引用 | 散落两处，格式未统一 |
| source_file_id / source_file_name | ❌ 单案例单文件假设硬编码（一个案例目录=一个输出） | **最大缺口**：多文件/多版本需要 Project/Document 层 |
| source_version | ❌ 无版本概念，重复运行直接覆盖 output/<case>/ | 需引入运行版本或时间戳目录 |
| source_excerpt | ✅ explicit_needs[].quote（但为模型转述，非严格原文切片） | quote 与原文的一致性无校验 |
| retrieval_score | —（检索模块尚不存在） | P1 新建 |
| citation_reason | ⚠️ 部分：priority reason 里有依据说明；capability reason 有裁定理由 | 无结构化字段 |

**关键事实**：现有引用链是「D 编号 → mapping.json → P 编号 → 材料」，链路完整但**三处断点**——① 分析输出无 P 编号直引；② 无文件/版本维度（单案例假设）；③ quote 是模型转述而非原文校验切片。

## 三、与方案 §8.3 实体的对照（已有 → 需新增）

| 方案实体 | 现有对应物 | 结论 |
|---|---|---|
| Project | ❌ 无（案例目录即事实上的隔离单元） | P0 新增：project_id=案例目录名即可起步 |
| Document | ⚠️ 隐含（一案例一文件） | P0 显式化 |
| DocumentVersion | ❌ 无（重跑覆盖） | P0 新增：输出目录加版本后缀或时间戳 |
| ParagraphMap | ✅ mapping.json（pid↔did），且 Segment 有 raw_text/text | **基本齐备**，差落盘 raw_text 对照 |
| RetrievalChunk | ❌ | P1 新建（先关键词，段落级即可，无需向量） |
| Conversation / Message / AnswerCitation | ❌ | P1 新建 |
| AnalysisRun / Requirement / HumanReview | ⚠️ analysis.json 隐含（meta 有版本无 run_id）；无人工修正留痕 | P0 补 run_id；HumanReview 属 P2 |
| ReportVersion | ⚠️ report.md 单版本覆盖 | P0 顺带版本化 |

## 四、现有测试清单（Task 1 验收项）

28 项 pytest，分布：

| 文件 | 覆盖 |
|---|---|
| test_cleaner.py | 清洗：分段、PII 脱敏、刷屏合并、编号映射 |
| test_scoring.py | 四维加权计算、档位边界 |
| test_validators.py | node1/node2 JSON Schema 校验、引用存在性校验（dids ⊆ 清洗段落集合） |
| test_gt_loader.py | GT 解析（main/validation/final 三种格式） |
| test_metrics_v15.py | ⑦ 完整率口径、拆分感知配对 |

**关键既有保障**：validators 已强制「AI 引用的 D 编号必须存在于清洗段落集合」——这就是 EVD-01（引用真实性检查）的雏形，问答链路可直接复用同一校验思路。

## 五、Task 2 数据契约草案（下一步落码依据）

```python
# V2 核心数据契约（P0 落码范围）
{
  "project_id": "case_01_制造业会议转写",          # P0: 沿用案例目录名
  "document": {"doc_id": "...", "filename": "...", "current_version": "v1"},
  "versions": [{
      "version_id": "v1",
      "cleaner_version": "当前未版本化，P0 补 CLEANER_VERSION 常量",
      "paragraph_map": [{"pid": "P1", "did": "D001", "raw": "...", "clean": "...", "pii": false}]
  }],
  "index": {"chunks": [{"chunk_id": "...", "dids": ["D001"], "pids": ["P1"], "text": "..."}]},
  "analysis_runs": [{"run_id": "20261020_165046", "prompt_version": "v1.7.1", "output_dir": "..."}]
}
```

落码原则（依方案 §8.2）：新增独立模块（`src/storage.py` 等），不改 cleaner/workflow 行为；旧输出目录继续兼容读取。

## 六、Task 1 结论

1. **地基比方案预估的好**：段落映射、引用存在性校验、成本留痕三大件已存在，P0 实际增量 = Project/Version 层 + 引用结构统一 + raw_text 落盘；
2. **最大风险点**：现有「一案例一文件一输出」的单文件假设渗透在 workflow/run_case/web_demo 的路径逻辑里，多文件支持需要小范围重构输出目录约定；
3. **下一步（Task 2）**：按上述契约建 `src/storage.py` + 版本化输出目录 + CLEANER_VERSION 常量，验收标准=旧流程零回归 + 新结构可从现有输出无损转换。
