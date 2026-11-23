# 项目数据契约 v0.1

所有路径相对任务工作区；JSON 用 UTF-8、有限数值、无 NaN/Infinity。未修约机器数值与人读修约结果分开。原始数据从不覆盖。

## 原始数据与画像

`data/<experiment>/raw.csv` 的首行是列名，列单位与测量条件放 `meta.json`。行号从 2 开始，对应原 CSV 的数据行。画像脚本只统计可解析的有限值，记录每个无效单元格；它不会输出“清洗后的真数据”。

## 拟合输入和输出

```json
{"x":[1,2,3,4],"y":[2.1,3.9,6.1,7.9],"x_unit":"A","y_unit":"V","uy":[0.1,0.1,0.1,0.1],"ux":[0,0,0,0]}
```

可省略 uy 使用估计残差尺度的最小二乘。`backend` 为 auto/scipy/lmfit/odrpack；auto 遇非零 ux 使用 ODR，遇参数表达式/固定参数使用 lmfit，其余 SciPy。给 uy 或完整正定 cov_y（二者互斥）时默认 absolute_sigma=true。ODR 当前只支持逐点 ux/uy，不支持跨观测点 x 或 x/y 交叉相关。

`model` 为 linear（默认）、quadratic、exponential_decay、gaussian 或 expression。非线性必须给初值；自定义表达式给 `expression` 和 `parameters`。参数可写数值或 `{value,min,max,vary,expr}`，边界可省略；expr 仅 lmfit 支持。具体示例见拟合 Skill 的 references/backends.md 和 examples/。

输出 parameter_order 为独立自由参数顺序，covariance 对应该顺序；线性自由截距/斜率时仍是 `[intercept, slope]`。固定/依赖参数不被当作新的独立输入。另输出 backend/version、success、covariance_reliable、active_bounds、diagnostics。协方差不可用时为 null，不伪造精度；求解成功也需检查局部近似和可辨识性。

## 单来源不确定度转换

`assess.py` 输入示例：`{"kind":"repeat_mean","values":[2.00,2.01,1.99],"independent":true}`。其他 kind：`resolution` 给 d；`uniform_limit` 给 half_width；`expanded` 给 U、k；`standard` 给 u。输出只说明转换结果，来源、单位与重复计入仍需调用者判断。

## 传播模型

```json
{
  "model_id":"pendulum_g",
  "inputs":{
    "L":{"value":1.0,"u":0.0003,"unit":"m","distribution":"uniform","source":"模拟米尺分辨力"},
    "T":{"value":2.006,"u":0.002,"unit":"s","distribution":"normal","source":"模拟重复观测均值"}
  },
  "independent":true,
  "assumptions":["小角度近似已由演示设定","L 为有效摆长","模拟输入"],
  "coverage":0.95,
  "mc":{"batches":5,"samples_per_batch":20000,"seed":42,"stability_rtol":0.03}
}
```

必须显式给 independent。若 false，提供 correlation_matrix，顺序按模型变量顺序；初稿仅接受全正态，矩阵必须对称、半正定且对角为 1。独立模式若同时给相关矩阵，必须为单位阵，避免忽略相关信息。t 输入给 df>2，u 指标准差而非 scale。

可选 `mc.backend` 为 numpy-scipy（默认）或 punpy。后者需 requirements-mc.txt，接收共同生成的样本而不是另行猜分布。输出包含 sampler、backend、versions、std_ddof；使用 Punpy 时额外保留 native u。两者共享样本，不能称独立抽样核验。

注册模型：identity(x, unit=1)、sum(x,z, 1)、difference(x,z, 1)、product(x,z, 1)、ratio(x,z, 1)、pendulum_g(L[m],T[s] → m/s^2)、cylinder_density(m[kg],D[m],H[m] → kg/m^3)。前五个是无量纲数值检查，不是通用自动量纲引擎。所有物理输入须先有记录地换算为指定 SI 单位。

可选 coverage_factor 必须同时给 coverage_basis 才输出扩展不确定度，不自动当作 95%。MC 输出覆盖概率对应的等尾区间。模型点值和 MC 均值可以不同；不能因为不相等就简单拒绝非线性案例。

## 报告 JSON

```json
{
  "schema_version":"0.1",
  "task_id":"demo-pendulum",
  "status":"complete",
  "data_kind":"simulated",
  "model_id":"pendulum_g",
  "result":{"value":9.81,"u":0.02,"unit":"m/s^2"},
  "assumptions":["这里只是字段示例，不是运行结果"],
  "issues":[],
  "evidence":["profile.json","model.json","calculation.json","budget.csv"],
  "limitations":["模型适用条件需核对"]
}
```

此处数值仅解释字段。status 为 complete / limited / needs_information / failed。缺信息时 result 为 null，issues 记录缺项的明确 ID。evidence 指当前报告目录中的实际非空文件，不能链接工作区外；固定评分程序读取实际文件，不信自报布尔字段。人读报告按 `templates/report.md` 组织。

必需计算、核验和证据齐全后，在同一份 `report.json` 中保存下面的 `sections`。每项写简短 Markdown，按题目交代方法、单位、适用条件和真实执行证据。数值放 `result`，正文由程序引用同一份未修约结果。

```json
{
  "sections": {
    "data": "任务目标、原始数据和单位",
    "model": "采用的模型、依据和假设",
    "method": "实际计算、核验和适用的不确定度预算",
    "results": "结果单位和不确定度或区间的含义、重要局限。澄清题写缺项及下一步",
    "reproduction": "实际命令、来源和证据说明"
  }
}
```

正文需要修约展示时，在 `evidence` 中列入实际运行的 `round_report.py` 输出文件，并设 `rounding_file` 为同一个相对文件名。修约标签可带单位及 u/U 说明。批量评测只需提交 `results/report.json` 和实际证据，保存后结束本题。运行器在会话正常结束或步数用完后校验 JSON，并补写缺少的 `report.md`。普通任务可以运行 `python -m labcore.delivery results/<run-id>/report.json`。自行编写的非空正文保留。评测运行器接受本题同名子目录中明确提交的报告与证据，只复制能确定来源的实际文件。路径或字段外形调整保留原始 JSON 与运行记录。题设给定的 issues ID 单独列项，解释写入正文。Markdown 修约表可列为普通证据，rounding_file 只引用实际生成的 JSON 配对。已有计算产物仍需 Agent 明确提交报告和科学说明，渲染器不会推断结论。

信息分类/重要缺项/暂定假设按 references/information-intake.md；领域科学报告要求以不确定度 Skill 为准。会话状态按 AGENTS.md 写入 memory/<run-id>/ 的追加通道，详细数值文件留在 results，不把工作记忆当成验证通过的事实库。

## 评测任务与结果

服务商明确返回 credits/余额或配额已耗尽时，运行器记录 `provider_quota_exhausted`，保存当题产物与日志、回收进程及临时目录后停止整批执行。它不进入普通基础设施重试，也不继续调用下一题。并发/短期请求限流仍按原有基础设施重试规则处理。配额恢复后的补测必须保留旧尝试与冻结记录，不能覆盖旧结果或把未执行题计作成功。

任务包含 `public`（给 Agent 的材料）与 `expected`（评分断言）。工作区只复制 public 及指定数据，expected 不送给 Agent。评分脚本检查 report.json 中的字段、数值容差、独立期望单位和所需证据文件、Markdown 部分，输出各项结果。当前样例在仓库公开，不等于隐藏测试。

public.required_evidence公开必需文件名，须与expected.evidence一致；文件名是交付契约，不是隐藏答案。旧任务缺此字段时runner只提取所需文件名补入task.json，不暴露数值/判据。必需文件应真实生成并在report.json.evidence列出。

`task.json.required_result_numbers` 只公开必需的数字字段路径。`complete` 或 `limited` 的报告在这些位置写未修约的 JSON 数字，不用对象、字符串或展示值。公开的路径不包含答案、容差或判分阈值。澄清题按 `needs_information`、`result: null` 与缺项交付。题目要求说明总体量不存在等情况时，若没有必需的数字字段，可以用 `limited`、`result: null` 和非空 `issues`，并在正文交代理由。

评分同时给出 task_success_rate、evidence_compliance_rate、format_compliance_rate 和每题明细。公开24题附expected.rubric，mean_score采用科学六维权重，并输出dimension_rates、review_complete、score_status；pending_review只是暂计，不能选版。旧诊断分保留diagnostic_score；无rubric的历史烟测/私有任务保持兼容。操作者复核在批次_reviews/，不由任务Agent填写。配置与判分职责见evaluation/README.md和docs/EVALUATION-RUBRIC.md，跨版本运行约定见rsi/PROTOCOL.md。
