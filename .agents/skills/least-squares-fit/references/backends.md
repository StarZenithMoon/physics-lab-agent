# 拟合后端与输入约定

在项目虚拟环境安装 `requirements.txt`，用 `.venv/Scripts/python.exe`（Windows）或 `.venv/bin/python` 执行。Skill 脚本需要项目 labcore 支持。

## 最小输入

```json
{"backend":"auto","model":"linear","x":[0,1,2,3],"y":[1.1,2.9,5.1,6.9],"uy":[0.1,0.1,0.1,0.1],"x_unit":"A","y_unit":"V"}
```

`uy` 是每点标准不确定度；`cov_y` 是所有 y 观测的完整正定协方差矩阵，二者互斥。已给 uy/cov_y 时默认 absolute_sigma=true；要按共同残差尺度估计时明确设 false 并解释。未给二者则估计残差尺度，不允许无依据要求绝对参数不确定度。

## 模型与初值

内置 `linear`（intercept,slope）、`quadratic`（intercept,linear,quadratic）、`exponential_decay`（offset,amplitude,tau）、`gaussian`（offset,amplitude,center,sigma）。线性可自动估初值，其余要求逐参数初值。

自定义模型：

```json
{
  "model":"expression",
  "expression":"offset+amplitude*exp(-x/tau)",
  "parameters":{
    "offset":{"value":0,"vary":false},
    "amplitude":{"value":2,"min":0},
    "tau":{"value":1,"min":0.001}
  }
}
```

同时给出 x、y、单位和误差信息；此片段只说明模型字段。支持 + - * / ** 以及 exp/log/sqrt/sin/cos/tan/abs、常数 pi/e；不执行任意 Python。物理定义域和单位一致性由 Agent 判断，不假装有自动量纲证明。

参数可写数值或带 value/min/max/vary/expr 的对象。`expr` 是相对其他参数的代数关系，用 lmfit。输出 parameter_order 仅列独立自由参数，协方差也是这一顺序。固定参数只有在确实无不确定度的前提下才能这样处理；已知但有不确定度的校准参数不应简单固定后遗忘。

## ODR

给出每点 ux 和 uy，误差分别为独立的标准差。ux=0 的点保持固定；其他点允许 x 调整。当前一维适配器不支持跨观测点相关或 x/y 交叉相关。求解目标包含 x/y 的加权调整量，不能只用竖直残差解释其卡方。

odrpack 的 cov_beta 未乘残差方差；默认绝对 uy/ux 时用此矩阵；relative 模式乘 res_var。其 sd_beta 是乘残差方差后的量，不能默认当作绝对观测尺度下的标准不确定度。

## 结果检查

保留 success、协方差、局部 Jacobian 的秩/条件数、触边标记及警告。非有限协方差输出 null；秩不足或触边时 covariance_reliable=false。该布尔值只是适配器诊断，不能证明非线性局部近似成立。失败不换模型或删除数据以凑结果。

官方依据：[SciPy curve_fit](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.curve_fit.html)、[lmfit](https://lmfit.github.io/lmfit-py/fitting.html)、[odrpack](https://hugomvale.github.io/odrpack-python/reference/)。
