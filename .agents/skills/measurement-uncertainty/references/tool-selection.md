# 不确定度工具选择

## 当前组合

- **NumPy + SciPy（默认）**：矩阵合成、显式分布抽样、统计与种子管理；覆盖当前标量模型和独立正态/矩形/t 输入、相关联合正态输入。
- **Punpy（可选，已提供适配器）**：高层传播接口。`mc.backend="punpy"` 时，将相同的 NumPy/SciPy 输入样本交其 propagate_standard/run_samples 执行。保留原始库的统计输出，同时统一使用样本 std(ddof=1) 报告 u；记录批次、种子和版本。

当前 Punpy 适配器的价值是统一调用与后续扩展传播管理，不宣称替换了整个抽样实现。两路公式/MC 的近似对照仍成立；两种 MC 后端之间因共享样本，不能充当独立概率模型验证。

选择后端必须显式，依赖不存在就报告，不静默回退。不允许丢弃无效模型样本；适配器在模型求值和返回时检查有限性/样本量。

## 当前不接入的工具

| 工具 | 面向问题 | 何时再考虑 |
| --- | --- | --- |
| UQpy | 一般物理/数学系统 UQ，多类采样和更复杂建模 | 仿真昂贵、代理模型或更大 UQ 工作流 |
| Uncertainpy | 黑箱模型 UQ、准 MC 和多项式混沌，面向计算神经科学 | 有对应黑箱模型/特征需求 |
| SALib | Sobol/Morris/FAST 等全局敏感性分析 | 要回答“哪些输入影响输出最大”，并满足所选方法的依赖假设 |

这些方法不等同于测量不确定度来源评定。也不把敏感性指数自动当作 GUM 预算百分比，尤其在非线性或相关输入下。

官方资料：[Punpy](https://punpy.readthedocs.io/en/latest/content/punpy_standalone.html)、[NumPy random](https://numpy.org/doc/stable/reference/random/index.html)、[SciPy stats](https://docs.scipy.org/doc/scipy/reference/stats.html)、[UQpy](https://uqpyproject.readthedocs.io/en/latest/)、[Uncertainpy](https://uncertainpy.readthedocs.io/en/latest/)、[SALib](https://salib.readthedocs.io/en/latest/)。
