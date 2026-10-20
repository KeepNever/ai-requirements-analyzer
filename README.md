# AI 客户需求分析助手

输入一封客户邮件、一段会议转写或一份微信记录，自动输出结构化需求分析报告：显性需求提取（带原文回链）、潜在需求推断、信息缺口与追问清单、四维优先级评分、能力匹配裁定。

LLM 应用求职作品集 · DeepSeek · 评测驱动迭代 · 17 案例三层验证集 · 28 项测试全绿。

成果演示页：`demo_成果演示.html`（浏览器直接打开）

## 快速使用

```bash
# 离线演示（无需 API Key，不花钱）
python src/run_case.py

# 真实分析一份客户材料（材料格式参照 test_data/final_validation/ 内案例：每段一行，段前标 P1、P2…）
python src/run_case.py --real --case 你的材料.md
```

产出在 `output/<案例名>/report.md`（人类可读报告）与 `analysis.json`（结构化数据）。
前提：项目根目录 `.env` 配置 `DEEPSEEK_API_KEY`。单案例成本约 0.3 元，耗时 1~2 分钟。

报告为草稿态：优先级结论、能力匹配、待确认分级等高风险字段须经人工审核后方可使用。

## 目录结构

| 位置 | 内容 |
|---|---|
| `src/` | 全部源代码 |
| ├ `src/pipeline/` | 分析管线：清洗（cleaner）、提示词（prompts）、评分（scoring）、校验（validators）、流程编排（workflow） |
| ├ `src/evaluation/` | 评测器：六指标计算（metrics）、GT 加载（gt_loader）、报告渲染（report） |
| ├ `src/run_case.py` | 单案例运行入口（日常使用只碰这个） |
| └ `src/run_eval.py` | 评测批量入口（main / dev / hold / final 四数据集） |
| `tests/` | pytest 单元测试（28 项） |
| `test_data/` | 案例材料与标准答案（GT） |
| ├ 根目录 | 主开发集 case_01~05 与对应 gt |
| ├ `validation/` | 开发/冻结验证集 dev_06~08、hold_01~03（GT v1.1） |
| └ `final_validation/` | 正式验证集 final_01~06（GT v1.0 已锁定，一次性盲测专用） |
| `docs/` | 全部文档 |
| ├ `docs/product/` | 项目与产品文档（01 项目定义 ~ 09 进度报告） |
| └ `docs/experiments/` | 评测与实验报告（10 M2 收口 ~ 25 M4-R2a，每轮实验含假设、变量、数据、判定） |
| `output/` | 运行产出（每案例一个文件夹；评测快照在 `output/evaluation/`） |
| `archive/` | 历史实验备份（如 M4-R1 基线 output 快照） |

## 评测体系速览

- **六指标**：① 提取 F1（0.79）② 关键遗漏率（盲测 0）③ 引用错位 ⑤ 优先级一致率（已知弱项，两轮实验挂账）⑥ 能力匹配一致率（0.61~0.86）⑦ 引用完整率
- **数据集纪律**：开发集反复回归；冻结集只作阶段性收口；正式盲测集用后即转固定回归集，绝不复用
- **版本管理**：Prompt / 评测器 / GT 三线独立版本号，git tag 冻结基线（m4-freeze），双轮验收抗运行噪声
- 详细演进见 `docs/experiments/` 编号顺序阅读
