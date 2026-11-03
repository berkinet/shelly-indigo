"""Gate and optionally publish a versioned release from a clean, tested commit."""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'berkinet/shelly-indigo'


def run(*args, capture=False):
    return subprocess.run(args, cwd=ROOT, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def release_plan(version, tag=None, notes_file=None, title=None):
    tag = tag or f'v{version}-beta.1'
    match = re.fullmatch(r'v(\d+\.\d+\.\d+)(?:-(alpha|beta|rc)\.([1-9]\d*))?', tag)
    if match is None or match[1] != version:
        raise ValueError('Tag must match PluginVersion, optionally followed by -alpha.N, -beta.N or -rc.N')
    notes = Path(notes_file) if notes_file else ROOT / f'docs/release-{tag[1:]}.md'
    prerelease = match[2] is not None
    label = f'Shelly {version}' + (f' {match[2]} {match[3]}' if prerelease else '')
    return tag, notes, title or label, prerelease


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--publish', action='store_true', help='Publish after all local gates and remote CI pass')
    parser.add_argument('--tag', help='Full tag, e.g. v0.2.2-beta.2 or v0.2.2; defaults to beta.1')
    parser.add_argument('--notes-file', type=Path, help='Defaults to docs/release-<full-version>.md')
    parser.add_argument('--title', help='Optional release title')
    args = parser.parse_args()
    if run('git', 'status', '--porcelain', capture=True).strip():
        raise SystemExit('Commit the intended source first; release gates require a clean worktree')
    commit = run('git', 'rev-parse', 'HEAD', capture=True).strip()
    info = plistlib.loads((ROOT / 'Shelly.indigoPlugin/Contents/Info.plist').read_bytes())
    version = info['PluginVersion']
    try:
        tag, notes, title, prerelease = release_plan(version, args.tag, args.notes_file, args.title)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if not notes.exists():
        raise SystemExit(f'Missing release notes: {notes.name}')
    checks = [('-m', 'unittest', 'discover', '-s', 'tests'), ('tools/check_bundle.py', '--source'),
              ('tools/build.py',), ('tools/check_bundle.py',)]
    for check in checks:
        run(sys.executable, *check)
    if run('git', 'rev-parse', 'HEAD', capture=True).strip() != commit or run('git', 'status', '--porcelain', capture=True).strip():
        raise SystemExit('Source changed during release validation')
    archive = ROOT / f'dist/Shelly-{version}.indigoPlugin.zip'
    manifest = archive.with_suffix('.manifest.json')
    manifest.write_text(json.dumps({'repository': REPOSITORY, 'commit': commit, 'tag': tag, 'version': version,
                                   'archive': archive.name, 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                                   'checks': [' '.join(check) for check in checks]}, indent=2) + '\n')
    print(f'Validated {tag} at {commit}; manifest: {manifest.name}', flush=True)
    if args.publish:
        remote = run('gh', 'api', f'repos/{REPOSITORY}/commits/main', '--jq', '.sha', capture=True).strip()
        if remote != commit:
            raise SystemExit('Push this commit to main before publishing')
        runs = json.loads(run('gh', 'run', 'list', '--repo', REPOSITORY, '--limit', '100',
                              '--workflow', 'checks.yml', '--json', 'headSha,status,conclusion,event', capture=True))
        if not any(item['headSha'] == commit and item['event'] == 'push' and item['status'] == 'completed'
                   and item['conclusion'] == 'success' for item in runs):
            raise SystemExit('The pushed commit must pass the complete Ubuntu/macOS CI workflow before publishing')
        refs = run('git', 'ls-remote', '--tags', 'origin', f'refs/tags/{tag}', capture=True).strip()
        if refs:
            raise SystemExit('Remote tag already exists; choose a new tag rather than reusing its source')
        existing = subprocess.run(['gh', 'release', 'view', tag, '--repo', REPOSITORY], cwd=ROOT,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if existing.returncode == 0:
            raise SystemExit('Release already exists; use a new tag instead of replacing its assets')
        run('gh', 'release', 'create', tag, str(archive), str(manifest), '--repo', REPOSITORY,
            '--target', commit, '--title', title, *(['--prerelease'] if prerelease else []),
            '--notes-file', str(notes))


if __name__ == '__main__':
    main()
