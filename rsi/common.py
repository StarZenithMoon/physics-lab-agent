import hashlib
import re
import math
import json
import os
from pathlib import Path
import yaml
from labcore.io import read_json
from labcore.project import ROOT

TARGET = ".agents/skills/measurement-uncertainty/SKILL.md"


def digest(path):
    if Path(path).is_dir():
        from .bundles import digest as bundle_digest
        return bundle_digest(path)
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_config(cfg, agent_steps):
    if cfg.get("target_skill") != TARGET:
        raise ValueError("this experiment only changes the selected uncertainty SKILL.md")
    for name in ("rounds", "model_steps", "timeout_seconds"):
        if type(cfg.get(name)) is not int or cfg[name] <= 0:
            raise ValueError(f"{name} must be a positive integer")
    if cfg["model_steps"] != agent_steps:
        raise ValueError("model_steps must match the Agent entrypoint steps; change both before the experiment")
    resources=cfg.get('resources',{})
    required={'memory_limit_mb','cpu_limit_percent','max_processes','max_log_mb','min_available_memory_mb','runtime_free_floor_mb'}
    optional={'startup_wait_seconds'}
    if not required<=set(resources) or set(resources)-required-optional or any(
            type(v) is not int or v<(0 if k in optional else 1) for k,v in resources.items()):
        raise ValueError('resources must specify positive integer memory/CPU/process/log and host memory limits')
    if not 128<=resources['memory_limit_mb']<=8192 or not 1<=resources['cpu_limit_percent']<=50:
        raise ValueError('task memory must be 128..8192 MiB and CPU 1..50 percent')
    if not 2<=resources['max_processes']<=64 or resources['runtime_free_floor_mb']>=resources['min_available_memory_mb']:
        raise ValueError('invalid process count or host memory reserve')
    if resources['min_available_memory_mb']<resources['memory_limit_mb']+resources['runtime_free_floor_mb']:
        raise ValueError('startup memory must cover the entire task hard limit plus the host reserve')
    for name in ("min_score_gain", "max_task_regression", "max_task_pass_rate_regression"):
        value = cfg.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be finite and nonnegative")
    if cfg["max_task_pass_rate_regression"] > 1:
        raise ValueError("max_task_pass_rate_regression must not exceed one")
    allowance = cfg.get("overall_observation_regression_allowance")
    if type(allowance) is not int or allowance < 0:
        raise ValueError("overall_observation_regression_allowance must be a nonnegative integer")
    return cfg


def config(root=None, environ=None):
    root = ROOT if root is None else Path(root).resolve()
    environ = os.environ if environ is None else environ
    cfg = read_json(root / "rsi/config.json")
    local = root / "rsi/config.local.json"
    if local.is_file():
        override = read_json(local)
        if not isinstance(override, dict) or set(override)-set(cfg):
            raise ValueError("config.local.json may only override declared project configuration fields")
        cfg.update(override)
    if environ.get("LAB_MODEL", "").strip():
        cfg["model"] = environ["LAB_MODEL"].strip()
    if cfg.get("model") is not None and (not isinstance(cfg["model"], str) or not cfg["model"].strip()):
        raise ValueError("model must be null or a nonempty provider/model ID")
    if not isinstance(cfg.get("agent"), str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", cfg["agent"]):
        raise ValueError("invalid Agent ID")
    agent = (root / ".opencode/agents" / (cfg["agent"] + ".md")).read_text(encoding="utf-8")
    meta = yaml.safe_load(agent.split("---", 2)[1])
    task_path = (root / cfg["tasks"]).resolve()
    if not task_path.is_relative_to((root/"evaluation").resolve()) or not task_path.is_file():
        raise ValueError("tasks must name an existing task file inside evaluation/")
    return validate_config(cfg, meta["steps"])


def fixed_digest(targets=None):
    from tools.package_source import source_files
    paths = list(source_files(ROOT))
    h = hashlib.sha256()
    # Hash effective experiment settings, not the location of private override files.
    h.update(json.dumps(config(), ensure_ascii=False, sort_keys=True).encode("utf-8"))
    for path in sorted(set(paths)):
        relative = path.relative_to(ROOT)
        if relative.as_posix() in set(targets or [TARGET]) or path.name.endswith(".local.json"):
            continue
        if relative.parts[0] == "data" and len(relative.parts) > 1 and relative.parts[1] in {"local", "private"}:
            continue
        h.update(relative.as_posix().encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def validate_candidate(path, expected_name="measurement-uncertainty"):
    if Path(path).is_dir():
        from .bundles import load
        return load(path)
    text = Path(path).read_text(encoding="utf-8-sig")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n(.+)\Z", text, re.S)
    if not match:
        raise ValueError("candidate must contain YAML frontmatter and a Markdown body")
    meta = yaml.safe_load(match[1])
    if not isinstance(meta, dict) or set(meta) != {"name", "description"}:
        raise ValueError("candidate frontmatter may only contain name and description")
    if meta["name"] != expected_name or not isinstance(meta["description"], str) or not meta["description"].strip():
        raise ValueError("candidate must preserve name and a nonempty description")
    if not match[2].strip():
        raise ValueError("candidate body is empty")
    return text
