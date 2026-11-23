"""Render an explicitly submitted scientific report without recalculating it."""
import argparse
import json
import math
from pathlib import Path, PureWindowsPath
import re
import shutil
import tempfile

from .io import read_json, write_json


SECTIONS = {
    'data': '数据与任务',
    'model': '测量模型与假设',
    'method': '计算与核验',
    'results': '结果与局限',
    'reproduction': '复现与来源',
}


def required_result_numbers(task):
    """Expose numeric field paths without reference values or tolerances."""
    checks = task.get('expected', {}).get('checks', [])
    return list(dict.fromkeys(check['path'] for check in checks
                             if check['path'].startswith('result.')
                             and any(key in check for key in ('target', 'min', 'max'))))


def evidence_file(directory, name):
    if not isinstance(name, str) or not name:
        raise ValueError('evidence names must be nonempty strings')
    relative = Path(name)
    path = directory / relative
    if (relative.anchor or PureWindowsPath(name).anchor or '..' in relative.parts
            or '..' in PureWindowsPath(name).parts or not path.resolve().is_relative_to(directory)):
        raise ValueError(f'evidence path must stay inside the report directory: {name}')
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f'missing or empty evidence: {name}')
    return path


def rounding_pairs(path):
    shown = read_json(path)
    if not isinstance(shown, dict) or not shown or any(
        not isinstance(pair, dict) or any(not isinstance(pair.get(name), str) for name in ("value", "u"))
        for pair in shown.values()
    ):
        raise ValueError("rounding_file must contain round_report.py output strings")
    return shown


def submission_relative(name):
    """Resolve submitted output paths in either report-relative or results-relative form."""
    if not isinstance(name, str) or not name:
        raise ValueError("evidence names must be nonempty strings")
    path = PureWindowsPath(name)
    if path.anchor or ":" in name or ".." in path.parts:
        raise ValueError(f"unsafe evidence path: {name}")
    parts = path.parts[1:] if path.parts[:1] == ("results",) else path.parts
    if not parts:
        raise ValueError(f"evidence must name a file: {name}")
    return Path(*parts)


def route_submission(directory, task, original_path):
    """Reconcile the flat and single task-directory conventions without changing results."""
    directory = Path(directory).resolve()
    canonical = directory / "report.json"
    task_dir = directory / task["id"]
    source = canonical if canonical.exists() else task_dir / "report.json"
    if not source.is_file():
        return None
    report = read_json(source)
    if not isinstance(report, dict) or "sections" not in report:
        return None
    if report.get("task_id") != task["id"]:
        raise ValueError("task_id must match the task")
    evidence = report.get("evidence")
    if not isinstance(evidence, list):
        raise ValueError("evidence must be a list of relative file names")
    required = set(task.get("expected", {}).get("evidence", []))
    copies = {}
    names = {}
    for name in evidence:
        rel = submission_relative(name)
        normalized = rel.as_posix()
        locations = [source.parent / rel, directory / rel]
        if len(rel.parts) == 1 and task_dir.is_dir():
            locations.append(task_dir / rel)
        actual = next((p for p in locations if p.is_file() and p.stat().st_size), None)
        if actual is None:
            raise ValueError(f"missing or empty evidence: {name}")
        evidence_file(directory, actual.relative_to(directory).as_posix())
        if normalized in required or normalized == f"{task['id']}/{rel.name}" and rel.name in required:
            target = directory / rel.name
        elif source == canonical:
            target = actual
        else:
            target = directory / rel
        if target.name in {"report.json", "report.md"} and target.parent == directory:
            raise ValueError("a report cannot be used as its own evidence")
        if actual != target:
            if target.exists():
                raise ValueError(f"ambiguous evidence at {target.relative_to(directory).as_posix()}")
            if target in copies and copies[target] != actual:
                raise ValueError(f"ambiguous evidence sources for {name}")
            copies[target] = actual
        names[name] = target.relative_to(directory).as_posix()
    report["evidence"] = list(dict.fromkeys(names.values()))
    if not required <= set(report["evidence"]):
        missing = sorted(required - set(report["evidence"]))
        raise ValueError(f"required evidence is not listed: {', '.join(missing)}")

    changes = {}
    if source != canonical:
        changes["report_from"] = source.relative_to(directory).as_posix()
    if copies or any(old != new for old, new in names.items()):
        changes["evidence_routes"] = {
            target.relative_to(directory).as_posix(): origin.relative_to(directory).as_posix()
            for target, origin in copies.items()
        }
        changes["evidence_names"] = {old: new for old, new in names.items() if old != new}
    issues = report.get("issues")
    if isinstance(issues, list):
        labels = [re.fullmatch(r"([a-z][a-z0-9_]*?)\s*[:：]\s*(\S[\s\S]*)", i) if isinstance(i, str) else None
                  for i in issues]
        if any(labels):
            changes["split_issue_labels"] = [label[1] for label in labels if label]
            report["issues"] = [label[1] if label else issue for issue, label in zip(issues, labels)]
            changes["issue_descriptions"] = [i for i, label in zip(issues, labels) if label]
    rounding = report.get("rounding_file")
    if rounding is not None:
        try:
            relative = submission_relative(rounding).as_posix()
            routes = {submission_relative(name).as_posix(): target for name, target in names.items()}
            target_name = routes.get(relative, relative)
            if target_name not in report["evidence"]:
                raise ValueError("rounding file is not declared evidence")
            route = directory / target_name
            rounding_pairs(copies.get(route, route))
        except ValueError:
            # The structured rounding table is optional. Keep the original
            # report and any existing display artifact for scientific review.
            changes["invalid_optional_rounding"] = rounding
            report.pop("rounding_file")
        else:
            if rounding != target_name:
                changes["rounding_route"] = target_name
                report["rounding_file"] = target_name
    if not changes:
        return None

    shutil.copyfile(source, original_path)
    for target, actual in copies.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(actual, target)
    write_json(canonical, report)
    return changes


def render_report(report_path, *, task=None):
    """Create missing Markdown for a complete submission. Keep authored Markdown."""
    report_path = Path(report_path)
    destination = report_path.with_name('report.md')
    report = read_json(report_path)
    if not isinstance(report, dict) or 'sections' not in report:
        return False
    sections = report['sections']
    if not isinstance(sections, dict) or any(not isinstance(sections.get(key), str)
                                            or not sections[key].strip() for key in SECTIONS):
        raise ValueError('sections must contain nonempty data, model, method, results and reproduction text')

    status = report.get('status')
    result = report.get('result')
    if status == 'needs_information':
        if 'result' not in report or result is not None or not isinstance(report.get('issues'), list) or not report['issues']:
            raise ValueError('needs_information requires result=null and nonempty issues')
    elif status == 'limited' and 'result' in report and result is None:
        if not isinstance(report.get('issues'), list) or not report['issues']:
            raise ValueError('limited with result=null requires nonempty issues')
    elif status not in ('complete', 'limited') or not isinstance(result, dict) or not result:
        raise ValueError('complete or limited requires a nonempty result object')

    if task is not None:
        if report.get('task_id') != task['id']:
            raise ValueError('task_id must match the task')
        if status != 'needs_information':
            for key in required_result_numbers(task):
                value = report
                for part in key.split('.'):
                    value = value.get(part) if isinstance(value, dict) else None
                if type(value) not in (int, float) or type(value) is float and not math.isfinite(value):
                    raise ValueError(f'{key} must be a finite JSON number')

    directory = report_path.parent.resolve()
    evidence = report.get('evidence')
    if not isinstance(evidence, list):
        raise ValueError('evidence must be a list of relative file names')
    for name in evidence:
        evidence_file(directory, name)
    for name in (task or {}).get('expected', {}).get('evidence', []):
        if name not in evidence:
            raise ValueError(f'required evidence is not listed: {name}')

    body = ['# 实验数据处理报告']
    for key, heading in SECTIONS.items():
        body.extend((f'## {heading}', sections[key].strip()))
        if key == 'results' and result is not None:
            exact = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
            body.append(f'未修约机器结果\n\n```json\n{exact}\n```')
            rounding = report.get('rounding_file')
            if rounding is not None:
                if rounding not in evidence:
                    raise ValueError('rounding_file must be listed in evidence')
                shown = rounding_pairs(evidence_file(directory, rounding))
                body.append('修约展示\n\n' + '\n'.join(
                    f"- {label} `({pair['value']} ± {pair['u']})`" for label, pair in shown.items()))
        if key == 'reproduction' and evidence:
            body.append('证据文件\n\n' + '\n'.join(f'- `{name}`' for name in evidence))

    if destination.is_file() and destination.stat().st_size:
        return False

    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write('\n\n'.join(body) + '\n')
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path, help='path to report.json')
    args = parser.parse_args()
    if args.report.name != 'report.json':
        parser.error('input must be report.json')
    try:
        written = render_report(args.report)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print('report.md 已生成' if written else 'report.md 已存在，或 report.json 未提供 sections')


if __name__ == '__main__':
    main()
