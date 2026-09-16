"""Release assets without storing credentials; manifests go to ignored dist/.

Build first with the checked-in PyInstaller spec. Commands are deliberately
separate: prepare, github-upload, gitee-upload, github-publish, verify.
Only publish updates/latest.json after verify succeeds.
"""
from pathlib import Path
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mercury_app.version import __version__

TAG = 'v' + __version__
DIRECTORY = ROOT / 'dist' / ('release-' + TAG)
ASSET = 'MIP-DragonScience-' + TAG + '.exe'
GH_REPO = 'dragonMaLong/mercury-smp-analyzer'
GE_REPO = 'dragonMalong/mercury-smp-analyzer'
GH_API = 'https://api.github.com/repos/' + GH_REPO
GE_API = 'https://gitee.com/api/v5/repos/' + GE_REPO


def sha(path):
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest().upper()


def notes():
    return (ROOT / 'updates' / ('release-' + TAG + '.md')).read_text(encoding='utf-8')


def github_headers():
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if not token:
        result = subprocess.run(
            ['git', 'credential', 'fill'], input='protocol=https\nhost=github.com\n\n',
            text=True, capture_output=True, timeout=20,
            env={**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'Never'},
        )
        credentials = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        token = credentials.get('password')
    if not token:
        raise RuntimeError('GitHub credential unavailable')
    return {'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json'}


def gitee_token():
    token = os.environ.get('GITEE_TOKEN')
    if not token:
        raise RuntimeError('GITEE_TOKEN unavailable')
    return token


def github_request(method, url, *, values=None, file_path=None):
    """Use verified Windows TLS; pass credentials on stdin, never argv/disk."""
    headers = github_headers()
    arguments = ['curl.exe', '--noproxy', '*', '--silent', '--show-error',
                 '--http1.1', '--connect-timeout', '30', '--max-time', '900',
                 '--request', method, '--config', '-', '--write-out', '\n%{http_code}', url]
    if method in ('GET', 'PATCH'):
        arguments += ['--retry', '3', '--retry-all-errors', '--retry-delay', '2']
    if values is not None:
        headers['Content-Type'] = 'application/json'
        arguments += ['--data-binary', json.dumps(values, ensure_ascii=True)]
    if file_path is not None:
        headers['Content-Type'] = 'application/octet-stream'
        arguments += ['--data-binary', '@' + str(file_path)]
    config = '\n'.join('header = ' + json.dumps(k + ': ' + v) for k, v in headers.items())
    result = subprocess.run(arguments, input=config, text=True, encoding='utf-8', errors='replace',
                            capture_output=True, timeout=930)
    if result.returncode:
        raise RuntimeError(f'GitHub transfer failed (curl exit {result.returncode})')
    body, status = result.stdout.rsplit('\n', 1)
    if not 200 <= int(status) < 300:
        raise RuntimeError(f'GitHub API returned HTTP {status}')
    return json.loads(body)


def payload(response):
    if not response.ok:
        # Never expose URLs, query strings, headers, or tokens on failure.
        raise RuntimeError(f'Release API returned HTTP {response.status_code}')
    return response.json()


def prepare():
    source = DIRECTORY / 'MIP综合分析-DragonScience.exe'
    target = DIRECTORY / ASSET
    if target.exists() and sha(source) != sha(target):
        raise RuntimeError('Existing versioned asset differs; refusing overwrite')
    if not target.exists():
        shutil.copy2(source, target)
    chunk_size = math.ceil(target.stat().st_size / 4)
    parts = []
    with target.open('rb') as file:
        for index in range(1, 5):
            content = file.read(chunk_size)
            path = DIRECTORY / (ASSET + f'.part{index:02d}')
            digest = hashlib.sha256(content).hexdigest().upper()
            if path.exists() and sha(path) != digest:
                raise RuntimeError('Existing part differs; refusing overwrite')
            if not path.exists():
                path.write_bytes(content)
            parts.append({'name': path.name, 'url': f'https://gitee.com/{GE_REPO}/releases/download/{TAG}/{path.name}', 'size': len(content), 'sha256': digest})
    combined = hashlib.sha256()
    for part in parts:
        with (DIRECTORY / part['name']).open('rb') as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b''):
                combined.update(chunk)
    assert combined.hexdigest().upper() == sha(target)
    manifest = {
        'version': TAG, 'release_name': 'MIP综合分析-DragonScience ' + TAG,
        'asset_name': ASSET,
        'release_url': f'https://github.com/{GH_REPO}/releases/tag/{TAG}',
        'download_url': f'https://github.com/{GH_REPO}/releases/download/{TAG}/{ASSET}',
        'github_release_url': f'https://github.com/{GH_REPO}/releases/tag/{TAG}',
        'github_download_url': f'https://github.com/{GH_REPO}/releases/download/{TAG}/{ASSET}',
        'gitee_release_url': f'https://gitee.com/{GE_REPO}/releases/tag/{TAG}',
        'gitee_download_parts': parts,
        'release_notes': notes(), 'published_at': datetime.now(timezone.utc).date().isoformat(),
        'sha256': sha(target),
    }
    (DIRECTORY / 'latest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)


def find_release(platform):
    if platform == 'github':
        releases = github_request('GET', GH_API + '/releases')
    else:
        releases = payload(requests.get(GE_API + '/releases', params={'access_token': gitee_token()}, timeout=30))
    return next((r for r in releases if r['tag_name'] == TAG), None)


def upload(platform):
    manifest = json.loads((DIRECTORY / 'latest.json').read_text(encoding='utf-8'))
    release = find_release(platform)
    if release is None:
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
        values = {'tag_name': TAG, 'target_commitish': commit, 'name': manifest['release_name'], 'body': notes(), 'prerelease': False}
        if platform == 'github':
            values['draft'] = True
            release = github_request('POST', GH_API + '/releases', values=values)
        else:
            release = payload(requests.post(GE_API + '/releases', json={**values, 'access_token': gitee_token()}, timeout=30))
    print(platform, 'release', release['id'], 'tag', TAG, flush=True)
    if platform == 'github':
        path = DIRECTORY / ASSET
        existing = next((a for a in release.get('assets', []) if a['name'] == ASSET), None)
        if existing:
            if existing.get('size') != path.stat().st_size or existing.get('digest', '').lower() != 'sha256:' + sha(path).lower():
                raise RuntimeError('Existing GitHub asset does not match local build')
            print('GitHub asset already verified', flush=True)
            return
        url = release['upload_url'].split('{')[0]
        asset = github_request('POST', url + '?name=' + ASSET, file_path=path)
        assert asset['size'] == path.stat().st_size
        assert asset['digest'].lower() == 'sha256:' + sha(path).lower()
        print('GitHub upload verified', asset['size'], asset['digest'], flush=True)
    else:
        for part in manifest['gitee_download_parts']:
            if any(a.get('name') == part['name'] for a in release.get('assets', [])):
                print('Gitee existing part; remote hash will be checked by verify:', part['name'], flush=True)
                continue
            path = DIRECTORY / part['name']
            assert sha(path) == part['sha256']
            print('Uploading Gitee', path.name, path.stat().st_size, flush=True)
            with path.open('rb') as file:
                from requests_toolbelt.multipart.encoder import MultipartEncoder
                body = MultipartEncoder(fields={
                    'access_token': gitee_token(),
                    'file': (path.name, file, 'application/octet-stream'),
                })
                payload(requests.post(
                    GE_API + f"/releases/{release['id']}/attach_files",
                    data=body, headers={'Content-Type': body.content_type},
                    timeout=(120, 600),
                ))
            print('Gitee uploaded', path.name, flush=True)


def publish_github():
    release = find_release('github')
    if not release:
        raise RuntimeError('Upload first')
    expected = sha(DIRECTORY / ASSET).lower()
    assert any(a.get('name') == ASSET and a.get('digest', '').lower() == 'sha256:' + expected for a in release['assets'])
    result = github_request('PATCH', GH_API + f"/releases/{release['id']}", values={'draft': False, 'make_latest': 'true'})
    print('Published', result['html_url'], flush=True)


def verify():
    manifest = json.loads((DIRECTORY / 'latest.json').read_text(encoding='utf-8'))
    sources = [('GitHub', manifest['github_download_url'], manifest['sha256'], (DIRECTORY / ASSET).stat().st_size)]
    sources.extend((p['name'], p['url'], p['sha256'], p['size']) for p in manifest['gitee_download_parts'])
    for name, url, expected, expected_size in sources:
        digest = hashlib.sha256()
        size = 0
        with requests.get(url, stream=True, timeout=(30, 60)) as response:
            if not response.ok:
                raise RuntimeError(f'{name}: download HTTP {response.status_code}')
            for chunk in response.iter_content(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        assert size == expected_size, (name, 'size mismatch')
        assert digest.hexdigest().upper() == expected, (name, 'SHA-256 mismatch')
        print('DOWNLOAD VERIFIED', name, size, expected, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['prepare', 'github-upload', 'gitee-upload', 'github-publish', 'verify'])
    command = parser.parse_args().command
    try:
        if command == 'prepare':
            prepare()
        elif command.endswith('-upload'):
            upload(command.split('-')[0])
        elif command == 'github-publish':
            publish_github()
        else:
            verify()
    except requests.RequestException as exc:
        print('Network request failed:', type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
