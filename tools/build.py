"""Build an installable plugin from checked-in source and portable dependencies."""
from pathlib import Path
from importlib.metadata import distributions
import plistlib
import shutil

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'Shelly.indigoPlugin'
BUILD = ROOT / 'build'
BUNDLE = BUILD / SOURCE.name


def main():
    packages = SOURCE / 'Contents/Packages'
    installed = {dist.metadata['Name'].lower().replace('_', '-'): dist.version
                 for dist in distributions(path=[str(packages)])}
    for requirement in (ROOT / 'requirements.txt').read_text().splitlines():
        if not requirement.strip() or requirement.startswith('#'):
            continue
        name, version = requirement.split('==')
        if installed.get(name.lower().replace('_', '-')) != version:
            raise RuntimeError(f'Missing or outdated {name}; run tools/vendor.py and commit its output')
    if list(packages.rglob('*.so')) or list(packages.rglob('*.dylib')):
        raise RuntimeError('Portable dependencies contain native extensions')
    if BUNDLE.exists():
        shutil.rmtree(BUNDLE)
    shutil.copytree(SOURCE, BUNDLE, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy(ROOT / 'LICENSE', BUNDLE / 'Contents/LICENSE')
    shutil.copy(ROOT / 'docs/third-party.md', BUNDLE / 'Contents/THIRD-PARTY.md')
    version = plistlib.loads((BUNDLE / 'Contents/Info.plist').read_bytes())['PluginVersion']
    (ROOT / 'dist').mkdir(exist_ok=True)
    archive = shutil.make_archive(str(ROOT / 'dist' / f'Shelly-{version}.indigoPlugin'),
                                  'zip', BUILD, BUNDLE.name)
    print(archive)


if __name__ == '__main__':
    main()
