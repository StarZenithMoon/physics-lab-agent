# 三 Skill RSI

RSI 流程用于在固定评测条件下改进三个领域 Skill，并记录候选、复核、运行与选版结果。候选生成使用 train 反馈，selection 用于候选比较，test 在候选选定后检查。

每轮只改一个 Skill，比较时保持任务、评分、模型和预算条件一致。三次重复描述运行波动。仓库随附的公开评测集共 24 题：train 10 题、selection 8 题、test 6 题。

| 文件 | 职责 |
| --- | --- |
| bundles.py | 建立三 Skill 快照并记录摘要 |
| optimization.json | 记录候选顺序、上下文预算和选版规则 |
| reflection.py | 从 train 评分和执行产物生成提议材料 |
| campaign.py | 管理候选、复核、运行和选版 |
| bridge.py | 验证继承基线所需的测量身份 |
| preflight.py / repeated.py / runner.py | 冻结条件、重复运行、逐题保存与进程回收 |
| versioning.py | 保存候选版本和对应评测记录 |
| ../evaluation/ | 评测题、参考值、量表和复核 |

通用操作约定见 [RSI 协议](PROTOCOL.md)，设计依据见 [RSI 研究说明](../docs/RSI-RESEARCH.md)。RSI 工具管理候选和评测记录，不训练模型权重。
