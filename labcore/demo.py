"""Reproducible scripted demonstration; not an LLM or RSI benchmark."""
import argparse
import csv
from pathlib import Path
from .io import read_json, write_json
from .data import profile_csv
from .uncertainty import assess_component, propagate
from .project import ROOT
from .reporting import rounded_standard


def run_demo(output, overwrite=False):
    output = Path(output)
    if output.exists() and any(output.iterdir()) and not overwrite:
        raise FileExistsError("output is not empty; use a new directory or --overwrite")
    output.mkdir(parents=True, exist_ok=True)
    source = ROOT / "data/demo-pendulum"
    meta = read_json(source / "meta.json")
    profile = profile_csv(source / "raw.csv")
    cycles = meta["cycles_per_reading"]
    periods = [v/cycles for v in profile["columns"]["total_time_s"]["values"]]
    t = assess_component({"kind": "repeat_mean", "values": periods, "independent": True})
    length = assess_component({"kind": "resolution", "d": meta["ruler_resolution_m"]})
    model = {"model_id": "pendulum_g", "inputs": {
        "L": {"value": meta["length_m"], "u": length["u"], "unit": "m",
              "distribution": "uniform", "source": "模拟米尺分辨力：d/sqrt(12)"},
        "T": {"value": t["value"], "u": t["u"], "unit": "s",
              "distribution": "normal", "source": "模拟重复单周期均值：s/sqrt(n)，正态近似"}},
        "independent": True, "assumptions": meta["assumptions"], "coverage": .95,
        "mc": {"batches": 5, "samples_per_batch": 20000, "seed": 42, "stability_rtol": .03}}
    calculation = propagate(model)
    a, m = calculation["analytic"], calculation["mc"]
    for name, obj in [("profile.json", profile), ("components.json", {"T": t, "L": length}),
                      ("model.json", model), ("calculation.json", calculation)]:
        write_json(output / name, obj)
    with (output / "budget.csv").open("w", encoding="utf-8", newline="") as stream:
        fields = ["input", "value", "unit", "u", "distribution", "source", "sensitivity", "variance_contribution"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(a["budget"])
    report = {"schema_version": "0.1", "task_id": "demo-pendulum", "status": "limited",
              "data_kind": "simulated", "model_id": "pendulum_g",
              "result": {"value": a["estimate"], "u": a["u"], "unit": a["unit"], "method": "first_order"},
              "assumptions": model["assumptions"], "issues": [],
              "evidence": ["profile.json", "components.json", "model.json", "calculation.json", "budget.csv"],
              "limitations": ["合成数据和简化预算，不代表真实实验全部来源已覆盖",
                              "MC 批次标准差诊断不等于覆盖区间端点精度认证"]}
    write_json(output / "report.json", report)
    y_text, u_text = rounded_standard(a["estimate"], a["u"])
    budget_rows = "\n".join(f"| {v['input']} | {v['distribution']} | {v['u']:.8g} | {v['sensitivity']:.8g} | {v['variance_contribution']:.8g} |" for v in a["budget"])
    text = f"""# 单摆数据处理演示

## 数据与任务

**本报告使用模拟数据，不是真实测量，也不是 Agent/RSI 评分。** 原始数据为十次测量，每次记录 {cycles} 个周期总时长，来自 data/demo-pendulum/raw.csv。

## 测量模型与假设

g = 4π²L/T²；T 为每次总时长除以周期数。假设与来源见 model.json。演示暂忽略的物理效应不能直接照搬到真实实验。

## 数据检查与拟合

原始读数及末尾零见 raw.csv。profile.json 提供数值画像，T 的标准差与均值标准不确定度见 components.json。本例不需要拟合。

## 不确定度预算

| 输入 | 分布 | 标准不确定度 | 灵敏系数 | 方差贡献 |
| --- | --- | --- | --- | --- |
{budget_rows}

## 计算与核验

真实脚本输出见 calculation.json。公式 u={a['u']:.9g}；MC u={m['u']:.9g}。
MC 样本量 {m['samples']}，种子 {m['seed']}，批次标准差稳定性诊断 {m['stable']}。
两路标准不确定度相对差 {calculation['comparison']['relative_u_difference']:.6g}；该数值只作诊断。

## 结果与局限

g = {y_text} m/s²，标准不确定度 u(g) = {u_text} m/s²（两位有效数字）。这不是 k=2 的扩展结果。
MC 的 95% 等尾覆盖区间约为 [{m['interval'][0]:.6g}, {m['interval'][1]:.6g}] m/s²；端点位数仅为数值展示，未认证相应精度。
状态为 limited：合成数据、简化来源、覆盖端点精度尚未验证；两路一致不能证明物理模型正确。

## 复现与来源

在项目目录运行 `python -m labcore.demo --out <新目录>`。代码 labcore/demo.py；原始运行输出 calculation.json；预算 budget.csv。
方法资料见 references/experiment-models.md 中 BIPM/JCGM 链接，模型按本演示设定。
"""
    (output / "report.md").write_text(text, encoding="utf-8")
    print(f"scripted_demo: {output.resolve()} (simulated; no LLM calls)")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="results/demo-pendulum")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    run_demo(args.out, args.overwrite)
