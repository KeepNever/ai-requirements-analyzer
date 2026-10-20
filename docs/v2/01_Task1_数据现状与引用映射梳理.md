# 01 · Task 1 交付：数据现状与引用映射梳理（v1.1 修订版）

> V2 主线 Task 1 ｜ 日期：2026-10-20 ｜ v1.1 依曦审阅意见补齐：数据流全图（现状+目标）与字段级数据契约
> 上游方案：`00_产品方案与迭代路线图_v1.0.md` §8.3 存储实体、§14 Task 1
> 状态：待曦验收。验收通过前不启动 Task 2 落码。

---

## 一、现状数据流（精确到文件与字段）

```text
材料文件（.md，P1/P2 编号段落，单案例单文件）
  │ cleaner.load_material(path)         截取「## 原始材料」之后内容；无标题则全文
  ▼
cleaner.clean(raw)                      纯代码节点，产出 CleanResult（内存对象，不直接落盘）
  │  CleanResult.segments: [{pid, did, text, raw_text, pii_scrubbed}]
  │     pid=P1…（原始编号）  did=D001…（清洗编号）
  │     text=脱敏后文本（进 LLM）  raw_text=脱敏前原文（留痕，当前不落盘）
  │  CleanResult.stats: {输入块数, 解析段落数, PII脱敏段落数, 刷屏合并数}
  ▼
workflow.run_case(path)                 输出目录 = output/<case_stem>/（重跑直接覆盖）
  ├─ mapping.json      {mapping:[{pid,did}], stats}          ← raw_text 未落盘
  ├─ analysis.json     {node1, node2, priority, meta{model,weights_version,mock}}
  │     node1.explicit_needs[]: {id, content, dids[], quote}
  │     node2.capability_match[]: {target, verdict, reason}
  │     node2.priority_dimensions[]: {id, impact, urgency, coverage, signal, reason}
  │     priority[]: 上述合成 + weighted + tier
  ├─ report.md         人类可读报告（重跑覆盖）
  └─ usage.json        成本记录
```

引用链现状：`dids → mapping.json 换算 → P 编号 → 材料`。链路通，但 **quote 是模型转述**，与原文的一致性无任何校验。

## 二、目标数据流（V2 结构）

```text
Project（projects/<project_id>/，隔离客户/项目）
  │
  ├─ documents/<document_id>/
  │    ├─ source.md                        原文，不可变（上传即定稿）
  │    └─ versions/<version_id>/
  │         ├─ clean.json                  清洗段落 + stats（含 raw_text / clean_text / pii 标记）
  │         └─ paragraph_map.json          P↔D 映射（含原文对照，可回链）
  │
  ├─ analysis_runs/<run_id>/               每次分析一个目录，永不覆盖
  │    ├─ meta.json                        {run_id, version_ids[], prompt_version, model, usage, status}
  │    ├─ analysis.json                    node1/node2/priority（结构同现状）
  │    └─ report.md                        该次运行的报告
  │
  └─ conversations/                        P1 阶段启用，本期不建（仅目录位保留）
```

关联规则：一次 AnalysisRun 绑定一个或多个 DocumentVersion（多材料支持后）；检索与问答（P1）基于 versions 的 paragraph_map + index 建设，**不**从某次 analysis.json 反向拼接。

## 三、数据契约（字段级，Task 2 落码依据）

> 通用约定：id 一律 slug 化（保留中文，禁 `\ / : * ? " < > |` 与空白）；所有时间戳 ISO 8601；所有 JSON 落盘 UTF-8 无 BOM。

### Project
| 项 | 约定 |
|---|---|
| 唯一标识 | `project_id`：目录名即实体，创建时查重 |
| 必须字段 | `project_id`, `name`, `created_at` |
| 存储位置 | `projects/<project_id>/` + `projects/index.json`（id→name 索引） |
| 生成者 | 用户创建项目时（Web / CLI） |

### Document
| 项 | 约定 |
|---|---|
| 唯一标识 | `document_id`：`文件名slug + "-" + 内容hash前8位` |
| 关联 | `project_id`（父目录） |
| 必须字段 | `document_id`, `project_id`, `file_name`, `current_version_id`, `uploaded_at` |
| 存储位置 | `projects/<project_id>/documents/<document_id>/`，`doc.json` 元数据 |
| 生成者 | 上传文件时 |

### DocumentVersion
| 项 | 约定 |
|---|---|
| 唯一标识 | `version_id`：`v1, v2…`（同一 document 内顺序号） |
| 关联 | `document_id`；`cleaner_version`（P0 新增常量 `CLEANER_VERSION`，cleaner 行为变更时递增） |
| 必须字段 | `version_id`, `document_id`, `cleaner_version`, `created_at`, `source_hash` |
| 存储位置 | `documents/<document_id>/versions/<version_id>/`（clean.json + paragraph_map.json），source.md 在上一级共享 |
| 生成者 | 上传时自动清洗生成 v1；材料修改后重传生成 v2 |
| 不可变 | 一经生成不可修改（修正 = 新版本） |

### ParagraphMap
| 项 | 约定 |
|---|---|
| 唯一标识 | `(version_id, clean_paragraph_id)` 复合键 |
| 必须字段 | `source_paragraph_id`(P), `clean_paragraph_id`(D), `raw_text`, `clean_text`, `pii_scrubbed` |
| 存储位置 | `versions/<version_id>/paragraph_map.json` |
| 生成者 | cleaner（v1.1 起 raw_text 落盘） |
| 用途 | 引用回链、EVD-01 引用真实性校验、检索切片的原文依据 |

### AnalysisRun
| 项 | 约定 |
|---|---|
| 唯一标识 | `run_id`：`run + 时间戳 + 短hash`（如 `run20261020_165046_a1b2`） |
| 关联 | `version_ids[]`（本次分析所依据的文档版本，多材料后为多个）；`prompt_version`（PROMPTS_VERSION）；`model` |
| 必须字段 | `run_id`, `project_id`, `version_ids[]`, `prompt_version`, `model`, `created_at`, `status`(running/done/failed), `usage` |
| 存储位置 | `projects/<project_id>/analysis_runs/<run_id>/` |
| 生成者 | 每次触发分析 |

### ReportVersion
| 项 | 约定 |
|---|---|
| 唯一标识 | `report_version_id`：默认等于 `run_id`（一次运行一份报告）；人工修正后的报告另起 `run_id + "-rev1"` |
| 关联 | `run_id` |
| 必须字段 | `report_version_id`, `run_id`, `path`, `created_at` |
| 存储位置 | 随 run 目录 `report.md` |
| 生成者 | 分析成功后 |

### 引用（Citation）字段规范
| 字段 | 约定 |
|---|---|
| `source_paragraph_id` | P 编号，由 did 经 paragraph_map 换算或分析时直接携带 |
| `clean_paragraph_id` | D 编号（现 explicit_needs[].dids） |
| `source_excerpt` | **必须由系统从 paragraph_map 的 raw_text 按段落切片生成**，与原文逐字一致；模型生成的 quote 只作展示字段 `quote_model`，不作为证据字段，不进入 EVD 校验 |
| `citation_reason` | 结构化理由（现散落 reason 文内，P0 起引用对象自带） |
| `retrieval_score` | **本契约不定义**。P1 检索模块上线时由检索结果自带，不为此预留字段、不提前实现向量检索 |

## 四、旧输出迁移规则（Task 2 验收项之一）

| 项 | 规则 |
|---|---|
| 映射 | 旧 `output/<case>/` → 新 `projects/<case>/`：一个 Project、一个 Document（test_data 源文件）、一个 DocumentVersion（v1）、一个 AnalysisRun（run_id 取 `run+`usage 时间戳） |
| 幂等 | 迁移前按 `source_hash + version 存在性` 判重，已迁移则跳过——重复执行不产生重复记录 |
| 兼容 | 旧 output/ 目录保留只读；现有 Web 路由（/report、/pipeline、/dashboard）继续可读旧结构；评测器（run_eval）不迁移，保持读旧输出 |
| 触发 | 提供 `python src/storage.py migrate` 手动触发，不做自动静默迁移 |

## 五、Task 2 范围与非目标

**范围内（四条，依曦审阅意见）**：
1. 新增 `src/storage.py`：定义上述六实体及存取（纯标准库，JSON 落盘）；
2. 版本化存储：原文/清洗/映射/分析/报告永不因重跑覆盖；
3. 旧输出迁移命令 + 幂等判重；
4. Workflow、Prompt、评测器零改动；28 项测试保绿；新增测试：版本保留、P/D 映射准确性、迁移幂等、旧输出兼容读取。

**非目标（下一阶段）**：RetrievalChunk、检索、对话消息存储、向量检索、AI 问答、HumanReview 界面。

## 六、Task 1 结论（v1.1 修订后）

1. 现有清洗/映射/校验三件套确认复用，不推翻 Workflow；
2. 结构缺口收敛为：Project/Version 层缺失、重跑覆盖、raw_text 未落盘——全部由 storage.py 一层解决；
3. 引用规范已定：证据字段以系统切片为准，模型转述降级为展示字段；
4. 验收通过后进入 Task 2，落码依据即本文第三节契约与第四节迁移规则。
