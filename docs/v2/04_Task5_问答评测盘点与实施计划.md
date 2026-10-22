# 04 · Task 5 第一轮盘点：问答评测实施计划（未落码）

> 依据：`00_V2后续优化与迭代路线图_完整方案.md` §8 / §17.1 ｜ 状态：待曦确认，确认前不写任何代码
> 本轮承诺：只盘点与制定计划，不修改生产问答逻辑、Prompt、EVD-01、需求分析主链路。

---

## 一、问答调用链盘点（§17.1 问题 1）

从用户提问到前端展示的完整路径：

```text
web_demo.py  Handler.do_POST
  └─ POST /api/ask
       ├─ _safe_pid(pid)                          路径安全校验
       ├─ _project_version_refs(pid)              取项目全部文档当前版本组合
       └─ retrieval.answer_question(...)          【核心，src/retrieval.py:152】
            ├─ storage._load_version_data(...)    逐版本加载 paragraph_map + clean.json
            ├─ retrieval.build_index(...)         segments → chunks（did/pids/text/raw_text）
            ├─ retrieval.keyword_search(...)      n-gram 停用词过滤 → top_k 命中
            ├─ 零命中回落（三层）                  会话历史引用段 → 小材料全量（≤40 段）→ 证据不足
            ├─ 生成答案
            │    ├─ mock:  _mock_answer(...)       拼接命中段（无 API）
            │    └─ real:  pipeline.llm_client.chat(messages, json_mode=True)
            │              messages = system(QA_SYSTEM) + 历史轮(≤5) + user(材料块+问题)
            ├─ _validate_cited_dids(...)          EVD-01：cited ⊆ (hits ∪ 回落段落)
            ├─ citations 组装                      storage._build_excerpts 逐段原文
            ├─ 会话落盘                            conversations/<conv_id>/messages.json（append）
            └─ 返回 → web_demo                    citations_for_frontend（脱敏）→ 前端气泡
```

## 二、当前可记录的运行数据与缺口（§17.1 问题 2）

### 已有（messages.json 每轮 assistant 消息）

| 字段 | 内容 | 评测可用性 |
|---|---|---|
| answer / sufficient | 校验后的最终回答与判断 | ✅ 可用于答案完整性与拒答准确率 |
| cited_dids / invalid_dids | 校验后有效引用 + 被剔除编号 | ✅ 引用有效率可直接算 |
| hits（did + score） | 当轮检索命中（含回落来源） | ✅ 检索召回率可直接算（对照 expected_citations） |
| version_refs | 本次使用的文档版本组合 | ✅ 多文档定位 |
| ts / prompt_version / mock | 时间、版本、模式标记 | ✅ 快照区分 |

### 缺口（评测运行需要补充记录，均为「新增记录」不改行为）

| 缺口 | 用途 |
|---|---|
| 发送给 LLM 的完整 messages（含材料块） | 定位错误层（证据没给到模型 vs 给了没用上） |
| 校验前的原始 LLM 输出（raw） | 区分「模型引用错误」与「EVD 剔除正确」 |
| 检索候选全文（hits 的段落文本） | 人工复核引用支撑率时的证据上下文 |
| 耗时（检索/LLM 分段）与 usage 成本 | 每轮报告的成本列 |
| 回落路径标记（hits 命中/历史回落/全量回落） | 归因「词汇鸿沟」类错误 |

**结论**：缺口全部可以通过在 `answer_question` 内增加「评测记录输出」（可选参数，默认关闭）补齐，不改任何现有行为。

## 三、独立评测运行入口设计（§17.1 问题 3）

```text
src/qa_eval.py（新增，CLI：python src/qa_eval.py --dataset test_data/qa_eval/dev.jsonl [--mock]）
  ├─ 读测试集 JSONL（每行一道题，字段见附录模板）
  ├─ 对每题调用 storage + retrieval.answer_question（现有签名，零改动）
  │    └─ 通过新增的可选参数 retrieval.answer_question(..., eval_trace=True) 拿到缺口数据
  ├─ 多轮题：脚本内按用例的 turns 数组连续调用（共享 conversation_id）
  ├─ 指标计算：自动算 §8.5 中可自动化的项；人工项输出复核模板
  └─ 快照落盘：output/evaluation/qa_runs/<run_ts>/（results.jsonl + report.md + meta.json），永不覆盖
```

关键设计：**评测是旁路**——只调用现有公开函数 + 新增记录参数，不 fork 问答逻辑，不改生产 Prompt/检索/EVD-01。

## 四、测试集文件结构（§17.1 问题 4）

```text
test_data/qa_eval/
  ├─ qa_eval_v0.1_dev.jsonl      20 题（DEV，可看逐题）
  ├─ qa_eval_v0.1_locked.jsonl   10 题（LOCKED，基线报告只出汇总分，逐题锁定）
  └─ README.md                    数据集版本、覆盖矩阵、起草纪律
```

- 每行一题，字段按方案 §18 模板（question_id / dataset_split / document_version_refs / question / expected_answer_points / expected_citations / evidence_sufficiency / expected_behavior / notes）。
- **覆盖矩阵**（方案 §8.3）：单事实 5 / 语义改写 4 / 多段汇总 4 / 多文档 4 / 多轮 4 / 证据不足 3 / 矛盾 3 / 汇总行动 3 = 30 题。
- 多文档题需要项目挂多文档——当前案例均为单文档，实施时选 2 个案例建一个多文档测试项目（存储层已支持 version_refs 多组合）。
- LOCKED 纪律由流程保证：评测脚本对 LOCKED 集只输出汇总指标；逐题明细写入单独加密命名文件（如 locked_detail_<hash>.json），基线报告阶段不打开。

## 五、指标自动化边界（§17.1 问题 5）

| 指标 | 自动/人工 | 计算依据 |
|---|---|---|
| 检索证据召回率 | ✅ 自动 | expected_citations 的段落 id ∈ 实际 hits 的比例 |
| 引用有效率 | ✅ 自动 | invalid_dids 为空 = 100%；有剔除则按剔除占比扣 |
| 拒答/不确定判断准确率 | ✅ 自动 | sufficient 与 evidence_sufficiency 的一致性（answerable↔true / insufficient,conflicting↔false） |
| 多轮追问通过率 | ⚠ 半自动 | 多轮脚本自动跑，最后轮引用有效性自动判；承接正确性留人工 |
| 引用支撑率 | ❌ 人工 | 编号存在 ≠ 内容支撑，必须逐条人工判 |
| 答案完整性 | ❌ 人工 | 对照 expected_answer_points 的要点覆盖，人工勾选 |
| 无依据陈述率 | ❌ 人工 | 需读答案找无出处的事实陈述 |
| 端到端通过率 | ⚠ 半自动 | 自动项全过 + 人工项无重大问题的题目占比，报告给槽位 |

人工复核输出方案 §18 的评审记录表（reviewer 槽位），每轮至少复核 10 题。

## 六、隔离保障（§17.1 问题 6）

| 需隔离对象 | 保障方式 |
|---|---|
| 需求分析 Prompt（v1.7.1） | 评测代码不 import prompts.py 的分析节点；问答用独立 QA_SYSTEM（已有） |
| metrics v1.5 | qa_eval 不调用 metrics 模块，问答指标独立计算 |
| GT v1.1/v1.0 | 需求分析 GT 目录不动；问答 GT 单独目录 test_data/qa_eval/，两套互不引用 |
| 历史快照 | 评测快照写 output/evaluation/qa_runs/<ts>/，追加不覆盖 |
| 生产行为 | 唯一生产代码改动 = answer_question 增加 `eval_trace=False` 可选参数（默认关闭，行为零变化）；由 diff 审查确认 |
| 数据集污染 | GT 起草纪律：先读材料独立写标准答案，再对照 AI 输出——写入 README 起草流程 |

## 七、实施计划（待确认后执行）

| 步骤 | 内容 | 交付 |
|---|---|---|
| 1 | GT 起草：按 §8.3 覆盖矩阵对 4 个代表案例（case_01/case_04/final_03/dev_06）+ 1 个多文档测试项目起草 30 题 | qa_eval_v0.1_dev.jsonl + locked.jsonl + README |
| 2 | `answer_question` 加 `eval_trace` 可选参数（输出检索候选全文/LLM messages/raw/耗时/回落路径） | retrieval.py 小改 + diff 审查 |
| 3 | 新增 `src/qa_eval.py` 评测运行器 + 快照 | CLI + 快照目录 |
| 4 | 新增 `tests/test_qa_eval.py`（评测器自身逻辑：指标计算、快照不覆盖、LOCKED 逐题隔离） | 测试 |
| 5 | 跑基线：DEV 20 题真实调用（约 0.2 元）+ LOCKED 10 题锁定跑分 | 基线报告 + 逐题错误明细 |
| 6 | 人工复核 10 题（引用支撑率/答案完整性） | 评审记录表 |

**预计成本**：GT 起草 + 基线真实调用合计 < 0.5 元。**预计文件变更**：新增 qa_eval.py、测试、3 个数据集文件；retrieval.py 增可选参数；web_demo/storage/metrics/prompts 零改动。

## 八、风险点

| 风险 | 应对 |
|---|---|
| 30 题覆盖不了全部错误类型 | 第一版按 §8.3 矩阵尽力覆盖；基线跑完后按错误分布补题（新版本号） |
| LOCKED 纪律靠自觉 | 脚本层面：LOCKED 逐题明细文件与基线报告分离输出；纪律写入 README |
| 多文档题依赖新测试项目 | 先做 2 案例合并项目；若多文档题起草困难，首版可按方案降级为规划项并明确标注 |
| eval_trace 参数被误用于生产行为变更 | 参数默认 False；diff 审查确认无行为分支 |
| 评测跑分受 LLM 随机性影响 | 关键版本重复跑 2 次比对，不稳定则标记（方案 §8.7 第 5 条） |

---

## 九、四审补充契约（2026-10-22，曦四审 4 项，全部纳入）

### 9.1 引用身份与召回率口径（钉死）

- **每条预期证据必须携带完整来源身份**：`{document_id, version_id, source_paragraph_ids(P 编号), clean_paragraph_ids(D 编号)}`——多文档场景下裸 D001/P1 不构成定位。
- **两种编号分工**：P 编号（原文证据编号）用于人工核对与 GT 追溯；D 编号（清洗后段落编号）用于与实际检索命中比对。
- **合并换算**：GT 中的 P 编号经对应版本 `paragraph_map` 统一换算为 D 集合后再参与比较——一个 P 被合并进某 D 时，命中该 D 即视为命中该 P 证据。
- **召回率公式**：`检索召回率 = 命中的预期证据数 / 全部预期证据数`（两侧均为去重后、带完整来源身份的证据集合）。
- **多点记录**：一题含多个必需证据点时，除总召回率外，记录 `all_evidence_hit`（布尔，是否全部命中），汇总时单独统计「全命中题占比」。

### 9.2 LOCKED 隔离纪律（落实版）

- 基线报告对 LOCKED 集**只输出汇总指标**，不展示逐题答案、引用、错误原因。
- 首轮人工复核 10 题从 **DEV 集**选取，不打开 LOCKED 逐题结果。
- LOCKED 逐题明细独立存储于 `output/evaluation/qa_runs/<ts>/locked_detail/`（常规 JSON，未实现加密——**不称加密文件**），并约定：基线阶段不读取；任何需要查看 LOCKED 逐题结果来定位错误的时刻，即将其身份**转为固定回归集**，此后不再作为盲测证据使用。
- 每次迭代保留独立检验能力的前提是 LOCKED 未被打开——写进评测 README 的操作纪律。

### 9.3 三态证据判定（answerable / insufficient / conflicting 分开）

| GT 题型 | 期望行为 | 自动指标 | 人工指标 |
|---|---|---|---|
| `answerable` | 有充分证据，正常回答并引用 | sufficient=true | 引用支撑率、答案完整性 |
| `insufficient` | 证据不足，明确说明无法确定 | sufficient=false | 「无法确定」是否说清 |
| `conflicting` | 识别矛盾并指出冲突及来源，**不擅自选边** | sufficient=false（基线口径） | **是否指出了矛盾及来源**（首轮人工） |

- 本轮基线**不修改 QA_SYSTEM**：conflicting 题基线口径为「sufficient=false 即及格；是否说明矛盾由人工复核判」。
- 基线结果预期：系统大概率在 conflicting 上失分——**这正是基线的价值**，用数据证明「矛盾识别」需要成为下一阶段优化项（先测清楚，再决定改哪层）。
- QA_SYSTEM 的 conflicting 显式支持属行为变更，留待基线报告出来、曦拍板后单独实施。

### 9.4 eval_trace 隐私与无行为变化标准

- `eval_trace=False`（默认）：零额外收集——返回值、Prompt、检索排序、EVD-01、会话记录行为与现状完全一致，由 diff 审查 + 回归测试双重确认。
- `eval_trace=True`：只记录评测所需数据（LLM messages、校验前 raw、检索候选全文、分段耗时、回落路径、usage）；trace 独立保存在评测运行目录 `output/evaluation/qa_runs/<ts>/traces/`，**不混入用户会话记录**（conversations/）。
- trace 文件不含 API Key 等任何凭证；含材料与模型原始输出，仅评测流程读取，**Web 前端不提供任何读取路径**。
- **会话隔离**：每道题建立独立会话 ID；仅同一道多轮测试题的多次调用共享会话（脚本内传递）；重复运行评测生成全新快照目录——上一轮对话历史不污染下一轮结果。
- 验证方式：diff 审查 + 两条自动化测试（trace 关闭时 messages.json 与 trace 文件均不产生额外字段；trace 打开时会话记录不含 trace 内容）。
