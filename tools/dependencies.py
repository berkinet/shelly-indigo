"""Verify pinned versions and the checked-in portable dependency contents."""
import hashlib
from importlib.metadata import distributions
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def pins():
    values = {}
    for line in (ROOT / 'requirements.txt').read_text().splitlines():
        if line.strip() and not line.startswith('#'):
            name, version = line.split('==')
            values[name.lower().replace('_', '-')] = version
    return values


def files(packages):
    return {str(path.relative_to(packages)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(packages.rglob('*')) if path.is_file()
            and '__pycache__' not in path.parts and path.suffix != '.pyc'}


def record(bundle):
    (bundle / 'DEPENDENCIES.json').write_text(json.dumps(files(bundle / 'Packages'), indent=2, sort_keys=True) + '\n')


def validate(bundle):
    packages = bundle / 'Packages'
    installed = {dist.metadata['Name'].lower().replace('_', '-'): dist.version
                 for dist in distributions(path=[str(packages)])}
    if installed != pins():
        raise ValueError('Bundled dependency versions differ from requirements.txt')
    locked = {name.lower().replace('_', '-'): version for name, version in re.findall(
        r'^([\w.-]+)==([\w.+-]+)', (ROOT / 'requirements.lock').read_text(), re.MULTILINE)}
    if locked != pins():
        raise ValueError('requirements.lock differs from requirements.txt; refresh upstream hashes')
    if list(packages.rglob('*.so')) or list(packages.rglob('*.dylib')):
        raise ValueError('Portable dependencies contain native extensions')
    expected = json.loads((bundle / 'DEPENDENCIES.json').read_text())
    if files(packages) != expected:
        raise ValueError('Bundled dependency contents differ from DEPENDENCIES.json')
