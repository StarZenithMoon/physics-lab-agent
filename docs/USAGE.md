# 使用说明

## 1. 跑通计算环境

按根目录 README 创建 `.venv`、安装 `requirements.txt`、运行测试和 `labcore.demo`。不需要模型、OpenCode、MATLAB、GEPA 或 Punpy 就能完成这一步。内部资源从代码位置定位，不依赖用户名和文件夹名称；命令中的输入/输出相对当前目录，因此统一在仓库根目录运行。

以下 `python` 指项目环境解释器。未激活时，PowerShell 用 `.\.venv\Scripts\python.exe`，Linux/macOS 用 `.venv/bin/python`。

## 2. 直接调用工具

```text
python .agents/skills/experimental-data-processing/scripts/profile.py data/demo-pendulum/raw.csv --out results/manual/profile.json
python .agents/skills/least-squares-fit/scripts/fit.py examples/fit-nonlinear.json --out results/manual/fit.json
python .agents/skills/measurement-uncertainty/scripts/propagate.py results/demo-current/model.json --out results/manual/calculation.json
```

传播示例使用先前演示实际生成的 model.json。单来源换算需先准备 JSON，例如 `{"kind":"resolution","d":0.001}` 保存为 `results/manual/component-input.json`，再运行：

```text
python .agents/skills/measurement-uncertainty/scripts/assess.py results/manual/component-input.json --out results/manual/component.json
```

工具只计算给定模型，不自动完成实验判断。另有 `examples/fit-constrained.json`、`fit-odr.json`；字段见 [输入输出契约](CONTRACTS.md)。

## 3. 用 Agent 组织完整任务

先在终端让项目 Python 位于 PATH 首位，再从该终端启动已安装并认证的 OpenCode。PowerShell 无需改执行策略：

```powershell
$env:PATH = (Join-Path (Get-Location) '.venv/Scripts') + [IO.Path]::PathSeparator + $env:PATH
python -c "import sys; print(sys.executable)"
opencode
```

Linux/macOS：

```bash
export PATH="$PWD/.venv/bin:$PATH"
python -c 'import sys; print(sys.executable)'
opencode
```

如果只有桌面版内置 CLI，可将可执行文件完整路径配置为 runner 的 `LAB_OPENCODE_EXE`，再按下一节批量执行。该变量不会创建 `opencode` 终端别名。已打开的桌面进程通常不会继承后来改的 PATH；需从项目环境重新启动。桌面模型选择也不会自动填写 runner 的模型配置。

选择 `lab-assistant`，先提交：

> 使用 data/demo-pendulum/ 的模拟数据和 meta.json 分析单摆实验；先检查数据和模型，按需选择 Skill，执行公式与蒙特卡洛传播，所有产物写 results/agent-demo-01/。保留证据，说明简化预算的局限。

与固定演示对照数值、方法、真实工具记录，不要求报告逐字相同。当前入口按 OpenCode V2 配置；旧版可能不兼容。路径/版本检查不等于权限和模型端到端验证。

## 4. 环境检查和评测

```text
python -m rsi.runner --doctor
python -m rsi.outer_loop --plan
python -m rsi.runner --split train --out evaluation/runs/prepared-01
```

以上不调用模型。prepared_only 不计入测评；doctor 检查 CLI、模型配置和数值库版本，不验证认证。

设置真实模型 ID：PowerShell 用 `$env:LAB_MODEL = 'provider/model'`；Linux/macOS 用 `export LAB_MODEL='provider/model'`。字符串是占位符，必须换成实际可用 ID。或创建不上传的 `rsi/config.local.json`：

```json
{"model":"provider/model"}
```

准备好实际模型调用后：

```text
python -m rsi.runner --split train --out evaluation/runs/v0-train --execute
```

runner 用启动它的 Python 环境，在临时工作区逐题执行。题目产物与 `_runner/<task-id>/` 中的请求/日志分开，`run_manifest.json` 保存实际环境。每次使用新输出目录。临时目录不是操作系统沙箱。完整选版见 [RSI 操作](../rsi/README.md)。

## 5. 自己的实验数据

放 `data/local/<experiment>/raw.csv`，旁边建 meta.json；可复制 [信息登记模板](../examples/experiment-meta.template.json)，填写列与单位、目标、重复测量方式、仪器规格原文/来源、校准/修正、共同误差和模型依据。null 表示未知，不要编造补齐；模板不是 propagate.py 的计算输入。

> 处理 data/local/my-experiment/ 的真实实验，先读 meta.json。关键条件缺失先集中澄清；原始数据只读，转换/拟合代码、预算、未修约计算和报告写 results/real-01/。待确认指标不要当作事实。

真实数据默认不上传；明确要求仓库外保存的数据，应放在单独的私有目录，不能放 data/local/。本项目打包器只带模拟数据和 data/public/ 中审核过的公开参考案例。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| ModuleNotFoundError | 回到仓库根目录，用项目 .venv 解释器 |
| CLI 找不到 | 安装并加入 PATH，或为 runner 设置 LAB_OPENCODE_EXE 完整路径 |
| 模型未配置 | 配置 OpenCode 认证，给 runner 实际 provider/model |
| 目录已存在 | 换 run-id；仅固定演示支持显式 --overwrite |
| Punpy 不存在 | 使用默认 numpy-scipy，或安装 requirements-mc.txt |
| helper 不支持物理模型 | 按 Skill 核实条件并编写本次有证据的计算；helper 不会自动推断模型/单位 |
