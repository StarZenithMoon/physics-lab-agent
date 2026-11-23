"""Project-specific scientific review; independent of task Agent outputs."""
import argparse
import hashlib
import json
from pathlib import Path
from labcore.io import read_json, write_json
from labcore.project import ROOT

VERSION = "scientific-rubric-v1"
DIMENSIONS = ("data", "model", "outcome", "method", "reproducibility", "reporting")
METHODS = {
    "fit": "核验权重/协方差缩放、参数顺序、收敛和残差；高相关系数不证明模型正确。",
    "covariance_convention": "与认证参考采用相同模型/残差尺度；缩放变量后正确变换参数及协方差。",
    "correlation": "保留配对或共享输入的协方差，不能擅自按独立来源合成。",
    "type_a": "区分单次标准差与均值标准不确定度；核验独立性、样本量和自由度。",
    "type_b": "保留规格/证书的来源及分布依据；不能将允差、步距或扩展U直接当标准u。",
    "coverage": "说明概率、单双侧及区间定义；正态、t或分位数的选择符合题设。",
    "monte_carlo": "核验实际抽样、联合分布、定义域、样本/种子及稳定性；均值和区间端点精度分别说明。",
    "prediction": "保留参数协方差，区分拟合均值与新观测预测的不确定度。",
    "model_limits": "如实指出外推/适用范围，限制影响不靠status标签代替说明。",
    "degrees_of_freedom": "总体方差未知的小样本与已知正态标准差分开处理，不机械要求或忽略自由度。",
    "resolution": "矩形舍入误差半宽为d/2，按题设转换，不能把d当半宽。",
    "corrections": "核验修正的符号与U/k转换；修正量与修正的不确定度分别记录。",
    "nonlinear": "说明f(E[X])与E[f(X)]及高阶影响，零梯度不意味着零不确定度。",
    "monte_carlo_limits": "解释总体矩的存在性；有限样本的有限标准差不能证明总体方差存在。",
    "errors_in_variables": "计入ux/uy的标准尺度与观测调整，不用仅竖直残差代替ODR目标。",
    "constraints": "保持给定参数关系，自由参数计数/协方差不把依赖参数重复计入。",
    "absolute_covariance": "已知绝对观测标准差时不按小残差再次缩小参数协方差。",
    "traceability": "疑似异常/完全重复仅标记，保留行号和原值，不能擅自删改。",
    "units": "换算过程可追溯，含导出量单位与协方差量纲。",
    "non_normal": "非正态覆盖区间按其分布求取，不能一律使用±2u。",
    "clarification": "识别决定性缺项、给最小补充问题，解释为何不能定量；不编造单位/规格/结果。",
}


def specification(task):
    caps = task.get("capabilities", [])
    method = " ".join(METHODS[c] for c in caps if c in METHODS)
    if not method:
        raise ValueError(f"no method rubric for {task['id']}")
    return {"version": VERSION, "criteria": [
        {"id": "data", "critical": True, "requirement": "列、单位、测量条件及来源与本题输入一致；转换/排除有记录，事实与假设分开。"},
        {"id": "model", "critical": True, "requirement": "被测量、模型/分析范围、修正和依赖关系有依据；只做题目要求，不虚构遗漏信息。"},
        {"id": "method", "critical": True, "requirement": method},
        {"id": "reproducibility", "critical": True, "requirement": "逐一核对代码/命令、真实轨迹、文件与报告数字；至少独立复算关键量。澄清题只需真实信息核对证据，不强制无意义数值代码。"},
        {"id": "reporting", "critical": False, "requirement": "结论、单位、不确定度/区间含义、依据与重要局限清楚；最终修约与未修约值分开。没有计算的澄清题明确缺项与下一步。标题齐全本身不代表内容正确。"},
    ]}


def bindings(task, directory):
    """Bind review to task, ALL outputs, and available harness trace (not only JSON)."""
    directory = Path(directory).resolve()
    values = {"task": hashlib.sha256(json.dumps(task, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink() or not path.resolve().is_relative_to(directory):
            raise ValueError("review artifacts must stay inside output directory")
        if path.is_file():
            values[path.relative_to(directory).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ("events.jsonl", "stderr.log", "request.json"):
        trace = directory.parent / "_runner" / task["id"] / name
        if trace.is_file():
            if trace.is_symlink() or not trace.resolve().is_relative_to(directory.parent):
                raise ValueError("review trace escapes run directory")
            values["_runner/" + name] = hashlib.sha256(trace.read_bytes()).hexdigest()
    return values


def review_template(task, directory):
    return {"task_id": task["id"], "rubric_version": VERSION, "reviewer": "", "artifact_sha256": bindings(task, directory),
            "rubric": {c["id"]: {"value": None, "reason": "", "evidence": "", "requirement": c["requirement"]}
                       for c in task["expected"]["rubric"]["criteria"]}}


def apply_rubric(task, directory, result, review):
    spec = task["expected"].get("rubric")
    if spec is None:
        return result
    if spec.get("version") != VERSION:
        raise ValueError("unknown scientific rubric version")
    weights = read_json(ROOT / "evaluation/scoring.json")["scientific_weights"]
    if set(weights) != set(DIMENSIONS) or any(type(v) not in (int, float) or not 0 <= v <= 100 for v in weights.values()) or sum(weights.values()) != 100:
        raise ValueError("invalid scientific rubric weights")
    valid = (isinstance(review, dict) and review.get("task_id") == task["id"] and
             isinstance(review.get("reviewer"), str) and bool(review["reviewer"].strip()) and
             review.get("rubric_version") == VERSION and review.get("artifact_sha256") == bindings(task, directory))
    records = review.get("rubric", {}) if valid else {}
    if not isinstance(records, dict):
        records = {}
    details = []
    for c in spec["criteria"]:
        r = records.get(c["id"], {})
        r = r if isinstance(r, dict) else {}
        v = r.get("value")
        ready = (type(v) in (int, float) and v in (0, .5, 1) and
                 isinstance(r.get("reason"), str) and bool(r["reason"].strip()) and
                 isinstance(r.get("evidence"), str) and bool(r["evidence"].strip()))
        details.append({"id": c["id"], "value": v if ready else 0, "critical": c["critical"],
                        "review_status": "recorded" if ready else "pending_or_stale",
                        "reason": r.get("reason", ""), "evidence": r.get("evidence", "")})
    if {d["id"] for d in details} != set(DIMENSIONS)-{"outcome"} or len(details) != 5:
        raise ValueError("rubric must contain five distinct review dimensions")
    rates = {d["id"]: d["value"] for d in details}
    checks = result.get("checks", [])
    rates["outcome"] = sum(c["passed"] for c in checks)/len(checks) if checks else 0
    rates["reproducibility"] *= result["evidence_compliance_rate"]
    ready = all(d["review_status"] == "recorded" for d in details)
    gate = result["gate_passed"] and ready and all(d["value"] == 1 for d in details if d["critical"])
    result.update(diagnostic_score=result["score"], score=sum(weights[k]*rates[k] for k in weights),
                  rubric_version=VERSION, rubric=details, dimension_rates=rates,
                  review_complete=ready, score_status="reviewed" if ready else "pending_review",
                  gate_passed=gate, task_passed=result["task_passed"] and gate)
    return result


def main():
    from .evaluate import load_tasks
    p = argparse.ArgumentParser(description="Create unfilled operator review templates; no model calls or automatic approval.")
    p.add_argument("--split", required=True, choices=["train", "selection", "test"])
    p.add_argument("--reports", type=Path, required=True)
    p.add_argument("--tasks", type=Path)
    args = p.parse_args()
    tasks = [t for t in load_tasks(args.split, args.tasks) if "rubric" in t["expected"]]
    repeat_file = args.reports/'repeat_manifest.json'
    roots = [args.reports]
    if repeat_file.is_file():
        manifest=read_json(repeat_file)
        if manifest.get('split')!=args.split:raise ValueError('split mismatch')
        roots=[(args.reports/r['reports']).resolve() for r in manifest['repetitions']]
        if any(not p.is_relative_to(args.reports.resolve()) for p in roots):raise ValueError('report path escapes repetition root')
    pending=[];skipped=[]
    for root in roots:
        for task in tasks:
            path=root/'_reviews'/(task['id']+'.json')
            if path.exists():raise FileExistsError(f'review exists; preserve it: {path}')
            directory=root/task['id']
            try:
                obj=read_json(directory/'report.json')
                if not isinstance(obj,dict):raise ValueError('invalid report')
            except (OSError,ValueError):
                skipped.append(str(directory));continue
            pending.append((path,task,directory))
    for path,task,directory in pending:
        write_json(path,review_template(task,directory))
    print(f'{len(pending)} pending templates; {len(skipped)} missing/invalid reports left as automatic failures; no grades assigned')
    return


if __name__ == "__main__":
    main()
