"""Refresh checked-in, unmodified Python dependencies; preserve upstream licenses."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
from dependencies import record

ROOT = Path(__file__).resolve().parents[1]


def main():
    target = ROOT / 'Shelly.indigoPlugin/Contents/Packages'
    with tempfile.TemporaryDirectory() as temporary:
        packages = Path(temporary) / 'Packages'
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check',
                        '--no-compile', '--no-binary=zeroconf', '--no-cache-dir',
                        '--target', str(packages), '--require-hashes', '-r', str(ROOT / 'requirements.lock')],
                       check=True, env={**os.environ, 'SKIP_CYTHON': '1'})
        if list(packages.rglob('*.so')) or list(packages.rglob('*.dylib')):
            raise RuntimeError('Portable dependencies contain native extensions')
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(packages, target, ignore=shutil.ignore_patterns('bin', '__pycache__', '*.pyc'))
    record(target.parent)
    print('Refreshed dependencies. Review and commit Contents/Packages with requirements.txt.')


if __name__ == '__main__':
    main()
