# 01 · Task 1 交付：数据现状与引用映射梳理（v1.2 修订版）

> V2 主线 Task 1 ｜ 日期：2026-10-20 ｜ v1.2 依曦二审意见修正 4 处契约冲突 + 2 处实现约定
> 上游方案：`00_产品方案与迭代路线图_v1.0.md` §8.3、§14 ｜ 状态：待曦验收，通过前不进 Task 2

---

## 一、现状数据流（精确到文件与字段）

```text
材料文件（.md，P1/P2 编号段落，单案例单文件）
  │ cleaner.load_material(path)         截取「## 原始材料」之后内容；无标题则全文
  ▼
cleaner.clean(raw)                      纯代码节点，产出 CleanResult（内存对象）
  │  segments: [{pid, did, text, raw_text, pii_scrubbed}]
  │  ⚠ 已证实缺陷：刷屏合并时被合并段落直接丢弃（仅计数），映射不留痕——
  │    两个 P1 合成一个 D001 后，第二个 P1 的原文在输出中不可追溯。
  │    该缺陷在 Task 2 修复（留痕增强 + CLEANER_VERSION 递增），详见 §3.2。
  ▼
workflow.run_case(path)                 输出 = output/<case_stem>/（重跑直接覆盖）
  ├─ mapping.json      {mapping:[{pid,did}], stats}
  ├─ analysis.json     {node1{explicit_needs[{id,content,dids[],quote}],...},
  │                     node2{potential_needs,pending_confirmations,
  │                           capability_match[{target,verdict,reason}],
  │                           priority_dimensions[...]}, priority[], meta}
  ├─ report.md         （重跑覆盖）
  └─ usage.json        成本
```

引用链现状：`dids → mapping 换算 → P 编号 → 材料`。链路通，但 quote 是模型转述、无原文校验、无文件/版本维度。

## 二、目标数据流（V2 结构，v1.2 修正目录）

```text
Project（projects/<project_id>/）
  │
  ├─ doc.json                            项目元数据
  ├─ documents/<document_id>/
  │    ├─ doc.json                       {document_id, project_id, file_name,
  │    │                                  current_version_id, uploaded_at}
  │    └─ versions/<version_id>/         每版本自包含，历史不可变
  │         ├─ source.md                 该版本原文快照（上传即定稿）
  │         ├─ clean.json                段落内容唯一数据源（见 §3.3）
  │         └─ paragraph_map.json        纯编号映射（支持多对一，见 §3.4）
  │
  ├─ analysis_runs/<run_id>/
  │    ├─ meta.json                      {run_id, project_id, version_ids[],
  │    │                                  prompt_version, model, status, usage, created_at}
  │    ├─ analysis.json                  结构同现状
  │    ├─ citations.json                 规范化引用（v1.2 新增落盘位置，见 §3.5）
  │    └─ report.md                      该次运行的报告
  │
  └─ conversations/                      P1 启用，本期不建
```

## 三、数据契约（v1.2 字段级）

> 通用：id 为 slug（禁 `\ / : * ? " < > |` 与空白）；时间 ISO 8601；JSON UTF-8 无 BOM。

### 3.1 Project / Document

| 实体 | 约定 |
|---|---|
| Project | `project_id`=目录名（创建时查重）；字段：project_id, name, created_at；位置 `projects/<project_id>/doc.json` + `projects/index.json` |
| Document | **`document_id` 为稳定标识：`文件名slug + "-" + 上传时间短串`，不含内容 hash**（v1.2 修正：hash 进 id 会导致内容变更即换 id，版本链断裂）。字段：document_id, project_id, file_name, current_version_id, uploaded_at |

**同名/同内容处理规则（v1.2 新增）**：
- `source_hash`（SHA-256）用于两件事：① 与同 Document 已有版本比对判断「内容是否真的变了」——未变则拒绝生成新版本（提示无变化）；② 全项目范围查重提示「该内容已存在于文档 X」。
- 上传同名文件且内容不同时，**由用户在界面选择**：「追加为文档 X 的新版本」或「新建独立文档」，不做静默默认。
- 上传内容与某已有版本完全一致：拒绝并提示，不生成空版本。

### 3.2 DocumentVersion（v1.2 修正：原文随版本走）

| 项 | 约定 |
|---|---|
| 唯一标识 | `version_id`：`v1, v2…`（document 内顺序号） |
| 关联 | document_id；`cleaner_version`（P0 新增常量） |
| 必须字段 | version_id, document_id, cleaner_version, source_hash, created_at |
| 目录 | 每版本自包含：`source.md`（该版原文快照）+ `clean.json` + `paragraph_map.json`——**修正 v1.1 的根目录共享 source.md 设计**，否则 v2 会覆盖 v1 原文 |
| 生成 | 上传时清洗生成 v1；重传修改版生成 v2；**同原文 + 新 CLEANER_VERSION 重新清洗时也生成新版本**（v1.2 新增：去重条件 = source_hash + cleaner_version 双因子，清洗规则升级的可追溯性优先于去重） |

### 3.3 clean.json（段落内容唯一数据源，v1.2 明确分工）

```json
{ "stats": {...}, "segments": [
  {"did": "D003", "raw_text": "原文（脱敏前）", "clean_text": "脱敏后（进LLM）",
   "pii_scrubbed": false, "merged_from": [] }
] }
```
- 段落文本内容**只存这里**（raw_text + clean_text），paragraph_map 不再重复存文本（v1.2 修正：消除双处存储不一致风险）。
- `merged_from`：被合并段落的原文列表（v1.2 新增，配合 cleaner 留痕增强——见 §3.4 附注）。

### 3.4 paragraph_map.json（纯编号映射，支持多对一）

```json
{ "version_id": "v1", "pairs": [
  {"clean_paragraph_id": "D003", "source_paragraph_ids": ["P3", "P4"]}
] }
```
- **v1.2 修正**：v1.1 的 `(version_id, did) + 单 source_paragraph_id` 无法表达刷屏合并的多对一关系，且现有 cleaner 合并时直接丢弃被合并段落。
- 采用「一 D 多 P」结构：`source_paragraph_ids` 含全部来源（含被合并者），数组首位为合并代表段。
- 复合键：`(version_id, clean_paragraph_id)`。
- **cleaner 留痕增强（Task 2 范围内的唯一 cleaner 改动）**：合并时不再丢弃，将原段落编号与文本记入 `merged_from`；LLM 输入形态不变（业务行为零改动）；`CLEANER_VERSION` 递增；配套单测覆盖多对一映射。

### 3.5 引用（Citation）规范（v1.2 修正：加入文档与版本维度）

| 字段 | 约定 |
|---|---|
| `document_id` | 来源文档（多文件场景下 P1/D001 会跨文档撞号，仅有编号无法定位） |
| `version_id` | 来源版本 |
| `source_paragraph_id` | 原文 P 编号（多对一时为首来源，或数组） |
| `clean_paragraph_id` | D 编号 |
| `source_excerpt` | 系统从对应版本 clean.json 的 raw_text 切片生成，逐字一致；**模型 quote 只作 `quote_model` 展示字段，不作为证据** |
| `citation_reason` | 该引用支撑什么结论 |

**落盘位置（v1.2 新增明确）**：Task 2 建 `citations.json`，随 run 目录存储——分析完成后由引用结构统一生成，问答（P1）与报告（P2）均从该文件读取，不再各自解析 analysis.json。
`retrieval_score` 本契约不定义，P1 检索上线时由检索结果自带。

### 3.6 AnalysisRun / ReportVersion（同 v1.1，无修改）

AnalysisRun：run_id=`run+时间戳+短hash`，关联 version_ids[]、prompt_version、model、status、usage；目录 `analysis_runs/<run_id>/`。
ReportVersion：report_version_id 默认=run_id，人工修正另起 `-rev1`。

## 四、旧输出迁移规则（v1.2 修正）

| 项 | 规则 |
|---|---|
| 来源定位 | 旧 `output/<case>/` → 源文件按精确名匹配 `test_data/<case>.md`（validation/final 案例查对应子目录）；**找不到源文件时迁移失败并明确报告、列出候选路径要求人工指定，不猜测**（v1.2 新增） |
| 缺失字段 | 旧记录缺 prompt_version、CLEANER_VERSION 等历史信息时记为 `"unknown"`，**不补造**（v1.2 新增） |
| 幂等 | 判重条件 = source_hash + cleaner_version + 已有 run 记录，重复执行不产生重复记录 |
| 兼容 | 旧 output/ 只读保留；Web 旧路由与评测器（run_eval）不迁移、照常读旧结构 |
| 触发 | `python src/storage.py migrate` 手动执行 |

## 五、Task 2 范围与非目标

**范围（含 v1.2 增量）**：
1. `src/storage.py`：六实体存取（纯标准库 JSON）；
2. 版本化存储：原文随版本走，永不覆盖；
3. cleaner 留痕增强：合并段落记录 merged_from（LLM 输入零变化）+ CLEANER_VERSION 常量；
4. `citations.json` 生成逻辑（从现有引用结构统一导出，行为无变化）；
5. 旧输出迁移 + 幂等；
6. Workflow / Prompt / 评测器零改动；28 测试保绿；新增测试：版本保留与读取、多对一 P-D 映射、多文件引用定位、迁移幂等、同名上传用户选择逻辑。

**非目标**：RetrievalChunk、检索、对话存储、向量检索、AI 问答、HumanReview 界面。

## 六、验收点（v1.2，依曦意见逐条对应）

1. 文档更新后生成新版本，旧原文与旧分析依然可读（§3.2 版本自包含）；
2. 同一版本 P-D 映射完整，支持多 P 合并一 D，且被合并段落可追溯（§3.4 + cleaner 增强）；
3. 多文件引用能准确定位到文档、版本、原文（§3.5 引用六字段 + citations.json）；
4. 历史迁移重复执行不重复创建记录（§4 幂等）；
5. 原 28 项测试全过，新增测试覆盖上述契约（§5 范围第 6 条）。
