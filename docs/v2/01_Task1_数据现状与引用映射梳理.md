# 01 · Task 1 交付：数据现状与引用映射梳理（v1.3 修订版）

> V2 主线 Task 1 ｜ 日期：2026-10-20 ｜ v1.3 依曦三审意见钉死最后 4 处：数据流图补齐、Citation 字段统一数组、迁移分层幂等、citations.json 生成边界与敏感原文保护
> 上游方案：`00_产品方案与迭代路线图_v1.0.md` §8.3、§14 ｜ 状态：待最终验收，通过即进 Task 2

---

## 一、现状数据流

```text
原始材料 .md
    ↓ cleaner.load_material()
提取原始材料正文
    ↓ cleaner.clean()
CleanResult（内存对象）
    ├── segments：pid / did / text / raw_text
    └── stats：清洗统计
    ↓ workflow.run_case()
output/<case_stem>/
    ├── mapping.json
    ├── analysis.json
    ├── report.md
    └── usage.json

重跑覆盖旧输出；raw_text 不落盘；
引用通过 did → mapping.json → pid → 原文回溯。
```

（「raw_text 不落盘」与「重跑覆盖」两处缺陷由 Task 2 修复，见 §3.2、§3.3、§4。）

## 二、目标数据流

```text
Project
    └── Document
         └── DocumentVersion
              ├── source.md
              ├── clean.json
              └── paragraph_map.json
                    ↓
              AnalysisRun
                    ├── meta.json
                    ├── analysis.json
                    ├── citations.json
                    └── report.md

每个版本保留原文、清洗结果和映射；
每次分析独立保存；
引用携带来源文档、版本及原文定位信息。

后续 P1：
DocumentVersion → 检索索引 → AI 对话
不从 analysis.json 反向拼接知识库。
```

## 三、数据契约（v1.3 字段级）

> 通用：id 为 slug（禁 `\ / : * ? " < > |` 与空白）；时间 ISO 8601；JSON UTF-8 无 BOM。

### 3.1 Project / Document

| 实体 | 约定 |
|---|---|
| Project | `project_id`=目录名（创建时查重）；字段：project_id, name, created_at；位置 `projects/<project_id>/doc.json` + `projects/index.json` |
| Document | **`document_id` 为稳定标识：`文件名slug + "-" + 上传时间短串`，不含内容 hash**。字段：document_id, project_id, file_name, current_version_id, uploaded_at |

**同名/同内容处理规则**：
- `source_hash`（SHA-256）用于：① 同 Document 内比对「内容是否真的变了」——未变则拒绝生成新版本；② 全项目查重提示「该内容已存在于文档 X」。
- 上传同名文件且内容不同：**由用户在界面选择**「追加为文档 X 的新版本」或「新建独立文档」，不做静默默认。
- 上传内容与已有版本完全一致：拒绝并提示，不生成空版本。

### 3.2 DocumentVersion（原文随版本走）

| 项 | 约定 |
|---|---|
| 唯一标识 | `version_id`：`v1, v2…`（document 内顺序号） |
| 关联 | document_id；`cleaner_version`（P0 新增常量） |
| 必须字段 | version_id, document_id, cleaner_version, source_hash, created_at |
| 目录 | 每版本自包含：`source.md` + `clean.json` + `paragraph_map.json`——历史原文不可被新版本覆盖 |
| 生成 | 上传时清洗生成 v1；重传修改版生成 v2；**同原文 + 新 CLEANER_VERSION 重新清洗也生成新版本**（去重双因子：source_hash + cleaner_version） |

### 3.3 clean.json（段落内容唯一数据源）

```json
{ "stats": {...}, "segments": [
  {"did": "D003", "raw_text": "原文（脱敏前，仅后端审计用）", "clean_text": "脱敏后（进 LLM 与前端展示）",
   "pii_scrubbed": false, "merged_from": [{"pid": "P4", "raw_text": "被合并段落原文"}] }
] }
```
- 段落文本**只存这里**，paragraph_map 不重复存文本。
- `merged_from`：被合并段落留痕（Task 2 的 cleaner 留痕增强，LLM 输入零变化）。

### 3.4 paragraph_map.json（纯编号映射，支持多对一）

```json
{ "version_id": "v1", "pairs": [
  {"clean_paragraph_id": "D003", "source_paragraph_ids": ["P3", "P4"]}
] }
```
- 一 D 多 P 结构：`source_paragraph_ids` 含全部来源（含被合并者），首位为合并代表段。
- 复合键：`(version_id, clean_paragraph_id)`。

### 3.5 引用（Citation）规范（v1.3 统一：来源字段一律数组，单复数不再并存）

```json
{
  "document_id": "customer-a",
  "version_id": "v1",
  "clean_paragraph_id": "D003",
  "source_paragraph_ids": ["P3", "P4", "P5"],
  "source_excerpts": [
    {"source_paragraph_id": "P3", "text": "对应 P3 的原文片段"},
    {"source_paragraph_id": "P4", "text": "对应 P4 的原文片段"},
    {"source_paragraph_id": "P5", "text": "对应 P5 的原文片段"}
  ],
  "citation_reason": "说明这些原文如何支撑当前结论"
}
```

| 字段 | 约定 |
|---|---|
| `document_id` / `version_id` | 引用身份的一部分——多文件场景下仅凭 P/D 编号无法定位来源 |
| `source_paragraph_ids` | 数组，多对一时含全部来源 P（v1.3 修正：不再有单数 `source_paragraph_id` 顶层字段） |
| `source_excerpts` | 数组，与 P 一一对应；**text 必须由系统从对应版本 clean.json 读取生成，不得由模型生成**；模型转述只作 `quote_model` 展示字段，不作为证据 |
| `citation_reason` | 该引用支撑什么结论 |
| `retrieval_score` | 本契约不定义，P1 检索上线时由检索结果自带 |

**citations.json 生成边界（v1.3 新增）**：
1. **只将能够从现有结构中可靠识别的 D 编号生成规范化引用**——即 `explicit_needs[].dids` 等结构化字段；`reason` 等自由文本中出现的编号不做正则捞取。无法可靠提取来源编号的结论不猜测、不伪造证据，在 citations.json 中记为 `unresolved` 状态（含结论 id 与原因），由测试明确覆盖。本阶段只生成引用数据，不改变 Prompt 与模型分析结论。
2. **敏感原文保护**：`raw_text` 可含未脱敏个人信息，仅限后端证据审计读取；**不得进入 AI 输入，不得直接渲染到前端引用卡片**。对外展示与问答默认使用脱敏后的 `clean_text`；引用卡片展示 `source_excerpts` 时做敏感扫描，命中 PII 的片段以脱敏版呈现。

### 3.6 AnalysisRun / ReportVersion（同 v1.2，无修改）

AnalysisRun：run_id=`run+时间戳+短hash`，关联 version_ids[]、prompt_version、model、status、usage；目录 `analysis_runs/<run_id>/`。
ReportVersion：report_version_id 默认=run_id，人工修正另起 `-rev1`。

## 四、旧输出迁移规则（v1.3 修正：分层幂等）

| 层 | 去重/恢复规则 |
|---|---|
| DocumentVersion | 同一 Document 下按 `source_hash + cleaner_version` 查找已有版本，命中即复用，不重建 |
| AnalysisRun | 为旧输出生成**确定性迁移键**（`migration_key` = 旧输出路径 + analysis/mapping/usage 三文件指纹的 hash）；已有相同 migration_key 的 Run 则复用；**上次迁移部分完成（如版本已建、run 未写）时，下次执行只补齐缺失部分，不整体跳过** |
| 多次运行 | 同一份材料的多次真实分析是合法的多条 AnalysisRun——**不能因 DocumentVersion 已存在而跳过尚未迁移的 AnalysisRun**；旧输出中每次可辨识的历史运行（按 usage.json 时间戳区分）各建一条 Run，无法辨识时合并为一条并记 unknown |

| 项 | 规则 |
|---|---|
| 来源定位 | 旧 `output/<case>/` → 源文件按精确名匹配 `test_data/<case>.md`（validation/final 查对应子目录）；找不到时迁移失败并明确报告、列出候选路径要求人工指定，**不猜测** |
| 缺失字段 | 旧记录缺 prompt_version、cleaner_version 等历史信息记为 `"unknown"`，**不补造** |
| 兼容 | 旧 output/ 只读保留；Web 旧路由与评测器不迁移、照常读旧结构 |
| 触发 | `python src/storage.py migrate` 手动执行 |

## 五、Task 2 范围与非目标

**范围**：
1. `src/storage.py`：六实体存取（纯标准库 JSON）；
2. 版本化存储：原文随版本走，永不覆盖；
3. cleaner 留痕增强：合并段落记入 merged_from（LLM 输入零变化）+ CLEANER_VERSION 常量；
4. `citations.json` 生成（仅结构化 dids 来源，unresolved 状态显式记录，敏感原文不出现在展示层）；
5. 旧输出迁移 + 分层幂等恢复；
6. Workflow / Prompt / 评测器零改动；28 测试保绿；新增测试：版本保留与读取、多对一 P-D 映射、Citation 定位到指定文档版本、部分迁移幂等恢复、敏感原文不进入展示输出、unresolved 引用记录。

**非目标**：RetrievalChunk、检索、对话存储、向量检索、AI 问答、HumanReview 界面。

## 六、验收点（v1.3，依三审意见）

1. 文档更新后生成新版本，旧原文与旧分析依然可读取；
2. 同一版本的 P-D 映射完整，支持多个 P 合并到一个 D，被合并段落可追溯；
3. Citation 能定位到指定文档、版本和原文（字段统一为复数数组，单复数不并存）；
4. 相同的历史迁移重复执行不重复创建记录；**部分迁移失败后可幂等恢复补齐**；
5. 敏感原文（raw_text）不会意外进入展示输出或 AI 输入；
6. 原有 28 项测试全部通过，新增测试覆盖上述数据契约（含 unresolved 引用记录）。

## 七、修订履历

| 版本 | 内容 |
|---|---|
| v1.0 | 初版：数据流、引用字段对照、缺口分析 |
| v1.1 | 一审：补目标数据流全图 + 六实体字段级契约 + 迁移规则 |
| v1.2 | 二审：document_id 去hash、原文随版本、多对一映射 + cleaner 留痕实证、引用六字段、去重双因子 |
| v1.3 | 三审：数据流图采用定稿版；Citation 来源字段统一数组；迁移分层幂等（migration_key + 部分恢复）；citations.json 生成边界（unresolved）与敏感原文保护 |
