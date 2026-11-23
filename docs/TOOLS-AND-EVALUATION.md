# 数值工具、RSI 操作与评测资源（2026-11-02）

拟合已在独立虚拟环境安装并接入 SciPy/lmfit/odrpack；MC 组合为 NumPy/SciPy 主线和可选 Punpy 适配器。GEPA、Pi、SForge 尚未接入运行。下文区分当前适配范围和库本身的能力。

## 拟合：调用成熟求解器

| 条件 | 后端建议 | 仍需 Skill 判断的事 |
| --- | --- | --- |
| 常规线性/非线性、主要是 y 方向误差 | SciPy curve_fit / least_squares | 模型依据、初值、权重、边界、收敛与残差；有依据时才解释参数协方差 |
| 命名参数、固定参数、参数关系、组合曲线 | lmfit（基于 SciPy） | 约束的物理含义、协方差缩放规则、参数可辨识性 |
| x/y 都有重要测量误差 | odrpack / 合适的误差变量方法 | 误差结构、权重和模型假设是否相容 |
| 已具备 MATLAB 环境 | MATLAB Engine + 相应拟合 Toolbox | MATLAB/Python 兼容、工具箱可用性及相同统计约定 |

[SciPy curve_fit](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.curve_fit.html) 支持标准差或观测协方差输入；absolute_sigma 影响参数协方差的缩放。非线性拟合中的局部协方差是近似，不能对所有曲线都当可靠不确定度。[lmfit](https://lmfit.github.io/lmfit-py/) 提供命名参数、边界、参数表达式和置信区间工具。[odrpack](https://hugomvale.github.io/odrpack-python/) 处理解释变量/响应变量的误差；[SciPy 已弃用 scipy.odr，推荐 odrpack](https://docs.scipy.org/doc/scipy/reference/odr.html)。

[MATLAB Engine](https://www.mathworks.com/help/matlab/matlab_external/install-the-matlab-engine-for-python.html) 能从 Python 调 MATLAB，需匹配 Python 与 MATLAB 版本；[Curve Fitting Toolbox](https://www.mathworks.com/products/curvefitting.html) 提供曲线/曲面拟合与图形工具。它是可选环境依赖，不当作默认开源后端。

本项目应统一输入：模型、参数初值/边界、观测及不确定度/相关结构；统一输出：参数、协方差、残差、求解状态、警告、版本及运行证据。保留一个对外 Skill，内部按条件选后端，不按软件品牌拆 Skill。

## 不确定度传播与蒙特卡洛

[Punpy](https://punpy.readthedocs.io/en/latest/content/punpy_standalone.html) 提供一阶 LPU 和 MC 传播等接口。当前已验收 1.1.0 的 MC 适配，使用共同 NumPy/SciPy 批次样本，检查模型有限性和返回数量。公式路径仍使用显式梯度。已经检查相关正态、矩形/t 混合、种子复现和退化输入；自适应覆盖端点精度仍未实现。

NumPy/SciPy 是基础抽样与计算工具；Punpy 是更高层传播工具。保持共同模型/输入，两路仍在同一不确定度 Skill 中。即使采用同一个库的两种方法，也只能说明方法对照，不能声称已建立完全独立的第三方核验。

拟合中的 MC/Bootstrap 是按有依据的数据生成/重采样模型反复拟合；得到参数后再传播到最终测量结果是另一层操作。不能直接混用 bootstrap 区间、参数置信区间和被测量覆盖区间。

[UQpy](https://uqpyproject.readthedocs.io/en/latest/) 是更一般的系统 UQ 工具；[Uncertainpy](https://uncertainpy.readthedocs.io/en/latest/) 有准 MC/多项式混沌并偏计算神经科学；[SALib](https://salib.readthedocs.io/en/latest/) 主要计算全局敏感性。当前标量实验传播不同时引入它们；按实际需求再扩展。

## RSI 的人、模型、程序分工

人先定并审核基准、预算与停止条件，随后启动。外层控制器调用提议模型改 Skill，再让任务 Agent 执行，固定 evaluator 判分，控制器决定接受/拒绝和保存版本。现在的初稿需要人手交接候选；后续建议把 [GEPA optimize_anything](https://gepa-ai.github.io/gepa/api/optimize_anything/optimize_anything/) 接为文本优化器。它接受文本候选、自定义评分和数据集划分；不自带本项目物理评分标准。

**GEPA** 可以理解为“自动试改多份文本并根据评测保留更好版本的优化器”。提供 Skill 初稿、训练/验证任务、执行/评分函数和用于提议的模型；它组织改写与搜索。改变的是文本与工作流，不是训练大模型权重，也不能替代领域评分的设计。

**[Pi](https://github.com/earendil-works/pi)** 是包含模型 API、Agent 循环、工具调用和交互 CLI 的 Agent 工具集。它与 OpenCode 更接近同一层的运行平台，不是拟合库或文本优化算法。当前已用 OpenCode，继续沿用即可；不因引入 GEPA 或参考 EdgeBench 就必须迁移到 Pi。

## EdgeBench 与其他参考：不要混为同一个项目

- [ByteDance Seed EdgeBench](https://github.com/ByteDance-Seed/EdgeBench)：测长时程环境反馈学习。可借鉴 SForge 的 work/judge 分离、提交反馈和过程曲线；当前文档包含 OpenCode 支持。它的工作对象是具体任务解，框架不自动把任务内迭代变成跨任务 Skill 优化。可自建实验题接评测层，但不能把自建/短预算实验报成官方分数。
- [Edge](https://getedge.cc/docs/tools/#benchmark)：独立的 Skill 发现与本地对照评测产品。其 benchmark 支持本地 Claude Code/Codex、自定义任务及 grader；不是 EdgeBench。该产品与 EdgeBench 是不同项目。
- [SkillsBench](https://github.com/benchflow-ai/skillsbench)：研究 Skill 对任务完成率的影响，适合参考同模型/同预算的有 Skill 与无 Skill 对照；不直接覆盖本项目所有实验。
- [Inspect AI](https://inspect.aisi.org.uk/scoring.html)：提供自定义 scorer 等评测基础设施，可作为更通用的后续选项；初期无需同时接多套框架。

若保留 OpenCode，建议先把领域评分做对，再评估是否需要 SForge 的容器运行层。三条件对照应分开：相同运行条件下的无 Skill、初始 Skill、调优 Skill。三轮改进曲线之外，最终留出任务上的成效更关键。

对本项目，EdgeBench 的用途是借鉴或复用 work/judge 隔离、统一执行预算、记录提交与反馈等基础设施。它不负责给普通物理实验选择传播模型，也不把同一题内改答案等同于跨题改进 Skill。当前阶段先用小规模领域评测；正式隐藏测试确有运行隔离需求时再考虑 SForge。自建题/缩短预算的成绩不称官方 EdgeBench 分数。

## 评分答案与判据的来源

1. [NIST StRD](https://www.nist.gov/itl/sed/products-services/statistical-reference-data-sets-strd)：线性/非线性回归和统计计算的认证参考值；须使用相同模型和计算目标，不能拿近似方法与不同定义的答案硬比。
2. [GUM / JCGM 100](https://www.bipm.org/en/doi/10.59161/JCGM100-2008E)、[JCGM 101](https://www.bipm.org/en/doi/10.59161/jcgm101-2008)：不确定度方法与例题依据，版本/补充见官方目录。
3. [NPLUnc_101](https://www.npl.co.uk/resources/software/measurement-uncertainty-evaluation)：复现 GUM 补充文件中的示例，提供独立参考；它有 Windows 示例程序和 MATLAB 函数形式，不假定为可直接 pip 安装的任意模型引擎。
4. 可解析模型和已知数据生成过程：验证均值、方差、相关性和方法适用边界；MC 的有限样本误差要纳入判据。
5. 教材与真实仪器资料：定义测量模型、重要修正和信息缺失应对；没有依据的实验事实不由 benchmark 框架自动补齐。

随仓库发布的公开评测集包含24题，按train、selection、test划分，案例范围见[案例索引](../evaluation/CASES.md)。公开题采用六维评分与逐题复核；旧版诊断分数不与科学量表混比。数值自动判，模型、方法和执行证据由任务外复核。评测说明见[评测设计](EVALUATION-DESIGN.md)。
