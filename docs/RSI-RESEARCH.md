# 轻量RSI的论文依据与实现取舍

核查日期：2026-11-05。只依据论文作者、会议论文集及官方实现/文档。下列采用方式是本项目工程选择，不是完整算法或论文效果复现。

| 一手资料 | 原机制 | 本项目采用 | 暂不采用 |
| --- | --- | --- | --- |
| [GEPA，ICLR 2026](https://proceedings.iclr.cc/paper_files/paper/2026/hash/0e9e708b6f48e14fd0ac29e167413f76-Abstract-Conference.html)；[官方实现](https://github.com/gepa-ai/gepa)；[组件选择API](https://gepa-ai.github.io/gepa/api/core/optimize/) | 从轨迹反思并修改复合系统提示组件，维护Pareto候选并组合经验；API支持组件选择、验证集与预算 | 三Skill映射为三个文本组件，逐轮单组件修改，训练反馈/执行片段、父子关系和全套验证 | 不称已实现GEPA；小预算下先用当前最优的坐标式优化，不接Pareto搜索、合并或新依赖 |
| [Self-Refine，NeurIPS 2023](https://papers.neurips.cc/paper_files/paper/2023/hash/91edff07232fb1b55a505a9e9f6c0ff3-Abstract-Conference.html) | 生成、反馈、修订循环，可不训练权重而改进输出 | 候选写训练依据假说，通过真实复测检验修改 | 原工作主要改任务输出；这里改跨任务Skill，反馈不能代替科学判卷 |
| [Reflexion，NeurIPS 2023](https://proceedings.neurips.cc/paper_files/paper/2023/hash/1b44b878bb782e6954cd888628510e90-Abstract-Conference.html)；[作者实现](https://github.com/noahshinn024/reflexion) | 语言反馈与情节记忆影响后续尝试 | 保存train失败、历史候选及train结果，继承可迁移经验 | 不以同题反复修改后的最好答案冒充pass@1，不让任务记忆读留出答案 |
| [Darwin Gödel Machine，作者论文](https://arxiv.org/abs/2505.22954)；[作者代码](https://github.com/jennyzzt/dgm) | 实证评估自修改并维护Agent档案、多路径探索 | 保存接受和拒绝版本、父版本、原始结果与源码 | 不开放任意自改Agent代码、工具或判卷，不实现开放搜索树 |
| [EdgeBench / SForge，官方仓库](https://github.com/ByteDance-Seed/EdgeBench)；[官方文档](https://bytedance-seed.github.io/EdgeBench/) | 长时程环境学习，work/judge双容器分离、迭代提交反馈 | 任务可见材料与判分分开、固定预算、完整记录并说明隔离限制 | 不迁移Linux/Docker长任务执行；Windows Job是资源限制，不是双容器隔离。自建物理题不报官方EdgeBench分数 |

## 为什么先保留轻量控制器

三个组件、三个候选的规模下，OpenCode执行、领域工具、哈希复核与Git留档可以复用。接入GEPA不会自动提供物理评分或消除泄漏，也需适配人工科学复核生命周期。先实现并测试组件接口；未来若接GEPA搜索层，应预注册新实验，不与当前测量混比。

完整bundle避免逐次改三个Skill时丢失已接受的改进。单组件变动利于归因，全套train/selection检查跨Skill影响。保留一分增益和逐题不退步是保守的项目策略；三遍波动可能降低接受率，不能看过候选分数后再放宽。

## 反馈与指标投机

训练包不只给总分，还给科学复核原因，并按固定规则选择最多三个train产物片段，限制片段/总上下文长度。新上下文生成候选；selection只选版，test split 最后检查。记录实际提议模型/会话、包与回答摘要，并做独立语义审核。

科学判分结合固定断言、真实轨迹和任务外量表复核。候选五项检查与任务六维评分分开，空模板不赋分。结构/链接校验无法证明语义，因此保留有依据的操作者审核。

六维权重15/20/25/20/15/5来自项目设计，论文不为该权重背书。数值项包含status和输出接口断言，不能读成纯数学正确率。本轮不通过改判卷制造提升。

## 基线与限制

优化器、快照与元数据修改可以在证明有效条件一致后继承已有基线：原evaluator重放原档、比对18个工作区和54次提示、核对运行/评分依赖和配置。保留测量身份和继承证书；任何实质变化都要求新基线，不自动忽略源码漂移。

现有题量小，数据处理专门题覆盖少，不能从总分归因全部Skill能力；该轮仍需全套回归并报告覆盖局限。三个候选不等于三个成功版本，三遍标准差不保证统计显著。设计者看过选版资料，AI辅助复核也有主观性；严格盲评仍需额外人员与隔离。

实现映射：`bundles.py`负责组件；`reflection.py`负责train-only反馈；`campaign.py`负责角色闸门和归档后晋级；`bridge.py`负责基线证书；`runner/process_guard/repeated`与`evaluation`沿用执行和领域判卷；`versioning.py`负责完整档案和提交恢复。GEPA/SForge均未作为运行依赖安装。
