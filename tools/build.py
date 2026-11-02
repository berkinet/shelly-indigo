"""Build an installable plugin from checked-in source and portable dependencies."""
from pathlib import Path
from dependencies import validate
import plistlib
import shutil

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'Shelly.indigoPlugin'
BUILD = ROOT / 'build'
BUNDLE = BUILD / SOURCE.name


def main():
    validate(SOURCE / 'Contents')
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
