"""Build an installable bundle with portable dependencies and their license notices."""
from pathlib import Path
import os
import plistlib
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'Shelly.indigoPlugin'
BUILD = ROOT / 'build'
BUNDLE = BUILD / SOURCE.name


def main():
    if BUNDLE.exists():
        shutil.rmtree(BUNDLE)
    shutil.copytree(SOURCE, BUNDLE, ignore=shutil.ignore_patterns('__pycache__', 'Packages', '*.pyc'))
    # Pure Python distributions keep the artifact usable on both Intel and Apple Silicon.
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check',
                    '--no-compile', '--no-binary=zeroconf', '--no-cache-dir',
                    '--target', str(BUNDLE / 'Contents/Packages'),
                    '-r', str(ROOT / 'requirements.txt')], check=True,
                   env={**os.environ, 'SKIP_CYTHON': '1'})
    if list(BUNDLE.rglob('*.so')) or list(BUNDLE.rglob('*.dylib')):
        raise RuntimeError('Portable bundle unexpectedly contains native extensions')
    shutil.copy(ROOT / 'LICENSE', BUNDLE / 'Contents/LICENSE')
    shutil.copy(ROOT / 'docs/third-party.md', BUNDLE / 'Contents/THIRD-PARTY.md')
    version = plistlib.loads((BUNDLE / 'Contents/Info.plist').read_bytes())['PluginVersion']
    (ROOT / 'dist').mkdir(exist_ok=True)
    archive = shutil.make_archive(str(ROOT / 'dist' / f'Shelly-{version}.indigoPlugin'),
                                  'zip', BUILD, BUNDLE.name)
    print(archive)


if __name__ == '__main__':
    main()
