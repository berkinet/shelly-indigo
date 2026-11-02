"""Validate installed source and release archives with an isolated Indigo-style loader."""
from pathlib import Path
from dependencies import validate as validate_dependencies
import argparse
import ast
import plistlib
import subprocess
import sys
import tempfile
import zipfile
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]


def validate_ui(server):
    tree = ast.parse((server / 'plugin.py').read_text())
    plugin = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'Plugin')
    methods = {node.name for node in plugin.body if isinstance(node, ast.FunctionDef)}
    for path in server.glob('*.xml'):
        root = ElementTree.parse(path).getroot()
        for field in root.iter('Field'):
            assert 'readOnly' not in field.attrib, f'{path.name}: use documented readonly attribute'
            for key in ('readonly', 'secure'):
                if key in field.attrib:
                    assert field.get(key) in ('true', 'false'), f'{path.name}: invalid {key}'
            callback = field.get('callbackMethod')
            assert callback is None or callback in methods, f'{path.name}: missing callback {callback}'
        for callback in root.iter('CallbackMethod'):
            assert callback.text in methods, f'{path.name}: missing callback {callback.text}'
        for listing in root.iter('List'):
            if listing.get('class') == 'self':
                assert listing.get('method') in methods, f'{path.name}: missing dynamic-list method'


def validate(bundle):
    info = plistlib.loads((bundle / 'Info.plist').read_bytes())
    assert info['ServerApiVersion'] == '3.9'
    assert info['PluginVersion'] == info['CFBundleVersion']
    validate_ui(bundle / 'Server Plugin')
    for source in (bundle / 'Server Plugin').rglob('*.py'):
        ast.parse(source.read_text())
    validate_dependencies(bundle)
    packages = bundle / 'Packages'
    for distribution in ('websocket_client', 'zeroconf', 'ifaddr'):
        metadata, = packages.glob(f'{distribution}-*.dist-info')
        assert any(p.name.upper().startswith(('LICENSE', 'COPYING'))
                   for p in metadata.rglob('*')), f'Missing license: {distribution}'
    # No preloaded Packages, no site-packages, no entry-point __file__, and an
    # unrelated working directory: both installed source and archives must work.
    script = """
from pathlib import Path
import logging
import sys
import types
server_plugin, packages = (Path(value).resolve() for value in sys.argv[1:])
sys.path.insert(0, str(server_plugin))
class Base:
    def __init__(self, unused_id, unused_name, unused_version, prefs):
        self.pluginPrefs = prefs
        self.logger = logging.getLogger('bundle-test')
        self.indigo_log_handler = logging.NullHandler()
        self.plugin_file_handler = logging.NullHandler()
sys.modules['indigo'] = types.SimpleNamespace(PluginBase=Base)
namespace = {'__name__': 'indigo_plugin_exec'}
source = (server_plugin / 'plugin.py').read_text()
exec(compile(source, 'plugin.py', 'exec'), namespace)
assert '__file__' not in namespace
plugin = namespace['Plugin']('test-id', 'test-name', 'test-version', {})
plugin.startup()
import websocket, zeroconf, ifaddr
for module in (websocket, zeroconf, ifaddr):
    assert Path(module.__file__).resolve().is_relative_to(packages), module.__file__
print('Indigo-style startup and bundled dependency imports passed')
"""
    with tempfile.TemporaryDirectory() as unrelated:
        subprocess.run([sys.executable, '-I', '-S', '-B', '-c', script,
                        str(bundle / 'Server Plugin'), str(packages)], cwd=unrelated, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', action='store_true', help='Validate the checked-in installable bundle')
    args = parser.parse_args()
    if args.source:
        validate(ROOT / 'Shelly.indigoPlugin/Contents')
        print('Validated repository plugin bundle')
        return
    version = plistlib.loads((ROOT / 'Shelly.indigoPlugin/Contents/Info.plist').read_bytes())['PluginVersion']
    archive = ROOT / 'dist' / f'Shelly-{version}.indigoPlugin.zip'
    with zipfile.ZipFile(archive) as package, tempfile.TemporaryDirectory() as temporary:
        assert not any('__pycache__' in name for name in package.namelist())
        package.extractall(temporary)
        validate(Path(temporary) / 'Shelly.indigoPlugin/Contents')
    print(f'Validated {archive.name}')


if __name__ == '__main__':
    main()
