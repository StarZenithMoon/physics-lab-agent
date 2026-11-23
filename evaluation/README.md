# 固定评测层

本层维护任务、参考答案与判据，独立于任务 Agent 的自检与 RSI 的候选提议。成功率、证据、留出、预算、失败安全与逐案分析均落实为可审查的任务检查。

| 文件 | 职责 |
| --- | --- |
| public-cases.json | 当前默认24题；public 给任务 Agent，expected 留给判卷进程 |
| tasks.json | 保留原6题脚手架，不再是默认评测集 |
| public-reference/ | NIST认证值、JCGM数值例题和自编题的答案依据，不复制进任务工作区 |
| oracles.py / standard_cases.py | 独立参考计算、题库构建与数值自检；不是任务 Agent 的工具 |
| scoring.json | 科学六维权重与兼容旧诊断权重；权重由项目定义 |
| rubric.py | 逐题科学要求、空白复核模板、全输出/轨迹绑定与六维判分 |
| evaluate.py | 读取真实报告与证据，逐题检查并聚合指标；不修改候选 Skill |

主要输出：task_success_rate、evidence_compliance_rate、format_compliance_rate 和每题明细。当前 task_passed 要求该题所有检查、所需证据及非空 Markdown 报告均存在/通过；这是题目产物约定，不等于物理真值认证。失败/缺报告记未通过。

required_evidence文件名契约和protocol.json固定三次/故障重试/失败规则；mc_validation.py与validation/mc-tolerance-v1.json提供无模型的容差确认。正式三次运行和汇总由rsi.repeated及outer_loop验证，单次evaluate仍可用于预试，不能代替正式批次。

公开24题使用 scientific-rubric-v1：数据15、模型20、结果25、方法20、复现15、表达5。数值自动判，五项内容须独立操作者复核，数据/模型/方法/复现是关键门槛。mean_score缺复核时只为暂计分；pending_review不能进入RSI。旧60/25/15保留为diagnostic_score，旧版诊断分数不与科学量表混比，不能混比。详见[科学量表](../docs/EVALUATION-RUBRIC.md)。

当前24题分为 train 10、selection 8、test 6；同一组变体不跨分组。它们都是公开开发题，不能称为严格隐藏测试。6个 NIST StRD、5个 GUM/JCGM 例题和13个自编合成边界题的逐题范围见 [案例索引](CASES.md)。每题记录模型、来源、期望、容差、失败条件和五项科学复核；文件存在和自述标记不是过程真实性证明。

```text
python -m evaluation.evaluate --split train --reports <运行报告目录> --out <评分输出.json>
```

实际运行后先以 `python -m evaluation.rubric --split train --reports <运行报告目录>` 建立空白 `_reviews/` 记录，再按量表填写；不会自动给通过分。复核绑定题目、全部输出及已有工具轨迹，不只绑定report.json。历史烟测100分不是新量表满分。

使用项目环境。实际 CLI 会话状态、耗时和固定文件检查由 rsi/runner.py 记录；评分文件不自行认证进程轨迹。固定评分与任务 Agent 分开运行，不以路径名或提示词宣称已建立系统级隔离。

```text
python -m evaluation.oracles --out results/reference-check.json
python -m rsi.runner --split train --out evaluation/runs/prepared-public-01
```

前者只检验参考值/数值适配，后者只准备请求，均不调用模型。运行结果保存在用户指定的输出目录。操作和框架选择见 [评测设计](../docs/EVALUATION-DESIGN.md)。
