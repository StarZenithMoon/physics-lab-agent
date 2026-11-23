"""Immutable three-Skill snapshots; one component may change per proposal."""
import hashlib
import json
from pathlib import Path
import re
import shutil

from labcore.io import read_json, write_json
from labcore.project import ROOT

SKILLS = ('measurement-uncertainty', 'least-squares-fit', 'experimental-data-processing')
PATHS = {name: f'.agents/skills/{name}/SKILL.md' for name in SKILLS}
FORMAT = 'three-skill-bundle-v1'
MAX_SKILL_BYTES = 64 * 1024


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def text_check(path, name):
    if Path(path).stat().st_size > MAX_SKILL_BYTES:
        raise ValueError('Skill text exceeds the frozen 64 KiB component budget')
    from .common import validate_candidate
    return validate_candidate(path, expected_name=name)


def load(directory):
    directory = Path(directory).resolve()
    if (directory/'bundle.json').is_symlink() or (directory/'bundle.json').stat().st_size>16*1024:
        raise ValueError('invalid bundle manifest')
    meta = read_json(directory/'bundle.json')
    if meta.get('kind') != FORMAT or set(meta.get('skills', {})) != set(SKILLS):
        raise ValueError('bundle must contain exactly the three declared Skills')
    allowed = {'bundle.json'}
    paths = {}
    for name in SKILLS:
        item = meta['skills'][name]
        relative = f'{name}/SKILL.md'
        if item.get('file') != relative:
            raise ValueError('bundle Skill paths are fixed and relative')
        path = directory/relative
        if path.is_symlink() or not path.resolve().is_relative_to(directory) or not path.is_file():
            raise ValueError('linked/missing Skill in bundle')
        if sha(path) != item.get('sha256'):
            raise ValueError('bundle content changed after registration')
        text_check(path, name)
        paths[name] = path
        allowed.add(relative)
    for path in directory.rglob('*'):
        if path.is_symlink() or path.is_junction():
            raise ValueError('bundle links are not allowed')
        if path.is_file() and path.relative_to(directory).as_posix() not in allowed:
            raise ValueError('bundle may not modify references, scripts or other files')
    return paths


def hashes(directory):
    return {name: sha(path) for name, path in load(directory).items()}


def fingerprint(values):
    return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def digest(directory):
    return fingerprint(hashes(directory))


def project_hashes():
    return {name: sha(ROOT/path) for name, path in PATHS.items()}


def create(output, parent=None, target=None, replacement=None):
    """Copy all components, with an optional one-file replacement; no live install."""
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('preserve existing bundle; choose a fresh path')
    originals = load(parent) if parent else {name: ROOT/path for name, path in PATHS.items()}
    if (target is None) != (replacement is None) or target is not None and target not in SKILLS:
        raise ValueError('specify one declared target and its replacement')
    if target:
        text_check(replacement, target)
    # Validate all sources before creating an output tree.
    for name, path in originals.items():
        text_check(path, name)
    output.mkdir(parents=True)
    for name, path in originals.items():
        destination = output/name/'SKILL.md'
        destination.parent.mkdir()
        shutil.copyfile(replacement if name == target else path, destination)
    write_json(output/'bundle.json', {'kind': FORMAT, 'skills': {
        name: {'file': f'{name}/SKILL.md', 'sha256': sha(output/name/'SKILL.md')} for name in SKILLS}})
    load(output)
    return output


def transition(parent, candidate, target):
    if target not in SKILLS:
        raise ValueError('unknown target Skill')
    before, after = hashes(parent), hashes(candidate)
    changed = [name for name in SKILLS if before[name] != after[name]]
    if changed != [target]:
        raise ValueError('exactly the declared one Skill must change from the current incumbent')
    return {'target': target, 'changed': changed, 'parent_hash': fingerprint(before),
            'candidate_hash': fingerprint(after), 'skill_hashes': after}


def structural_audit(parent, candidate, target):
    """Mechanical guard only; scientific semantics still require an explicit review."""
    transition(parent, candidate, target)
    old = load(parent)[target].read_text(encoding='utf-8-sig')
    new = load(candidate)[target].read_text(encoding='utf-8-sig')
    headings = re.findall(r'^##\s+(.+?)\s*$', old, re.M)
    new_headings = re.findall(r'^##\s+(.+?)\s*$', new, re.M)
    if not set(headings) <= set(new_headings):
        raise ValueError('preserve the original Skill section structure, including optional resources')
    # Skill-relative resources remain at the original installed path, not inside the bundle.
    old_links = set(re.findall(r'\]\(([^)]+)\)', old))
    new_links = set(re.findall(r'\]\(([^)]+)\)', new))
    if not old_links <= new_links:
        raise ValueError('preserve existing supporting-resource links')
    return {'kind': 'mechanical_only_not_scientific_approval', 'preserved_headings': headings,
            'preserved_links': sorted(old_links)}


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True,help='new initial three-Skill snapshot directory; no model calls')
    args=parser.parse_args()
    result=create(args.out)
    print(json.dumps({'bundle':str(result),'hash':digest(result),'model_calls':0},ensure_ascii=False))
