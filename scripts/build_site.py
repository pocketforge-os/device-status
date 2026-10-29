#!/usr/bin/env python3
"""Validate public sources and stage only the explicit portable site files."""
import argparse
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from urllib.parse import unquote, urlsplit

SITE_FILES = ('index.html', 'provenance.json',
              'trimui-smart-pro/hardware-status.html', 'trimui-smart-pro/hardware-status.json')
SOURCE_FILES = set(SITE_FILES) | {'README.md', '.gitignore', 'scripts/build_site.py',
                                'tests/test_site.py', '.github/workflows/pages.yml'}
PRIVATE = re.compile(r'/(?:home|root|run|tmp|var)/|\.lan\b|\b(?:pft-|tsp-)|\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b|\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b')
HEX = re.compile(r'[0-9a-f]{64}')
ROW_KEYS = {'id', 'name', 'scope', 'state', 'build', 'evidence_recorded_at', 'basis',
            'record_age_days', 'summary', 'limitations', 'next_validation', 'evidence_digests'}


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def keys(value, expected):
    require(isinstance(value, dict) and set(value) == set(expected), 'Unexpected public JSON fields.')


def public_strings(value):
    if isinstance(value, str):
        require(not PRIVATE.search(value), 'Private metadata in public JSON.')
    elif isinstance(value, dict):
        for key, item in value.items():
            public_strings(key)
            public_strings(item)
    elif isinstance(value, list):
        for item in value:
            public_strings(item)


def public_shape(data):
    keys(data, {'schema_version', 'device', 'generated_at', 'snapshot_at', 'input_sha256',
                'acceptance_note', 'hardware_counts', 'hardware', 'priorities'})
    require(data['schema_version'] == 1, 'Unsupported public data version.')
    keys(data['device'], {'name', 'soc', 'gpu', 'scope'})
    require(isinstance(data['hardware'], list) and isinstance(data['priorities'], list), 'Invalid public rows.')
    rows = list(data['hardware'])
    for goal in data['priorities']:
        keys(goal, {'id', 'name', 'scope', 'rows'})
        require(isinstance(goal['rows'], list), 'Invalid product rows.')
        rows.extend(goal['rows'])
    for row in rows:
        keys(row, ROW_KEYS)
    require(isinstance(data['hardware_counts'], dict)
            and all(k in ('Working', 'Partial', 'Detected', 'Broken', 'Untested', 'Parked', 'HW-limit', 'Deferred')
                    and type(v) is int and v >= 0 for k, v in data['hardware_counts'].items()), 'Invalid status counts.')


class StaticHTML(HTMLParser):
    def __init__(self, name):
        super().__init__(convert_charrefs=True)
        self.name = name

    def handle_starttag(self, tag, attrs):
        require(tag not in {'script', 'link', 'img', 'iframe', 'object', 'embed', 'audio', 'video', 'form', 'base'},
                'Active or external HTML resource.')
        for key, value in attrs:
            require(not key.startswith('on') and key not in {'src', 'srcset', 'action', 'http-equiv'},
                    'Active HTML attribute.')
            if key == 'href':
                require(tag == 'a' and isinstance(value, str), 'Invalid static link.')
                url = urlsplit(value)
                path = unquote(url.path)
                require(not url.scheme and not url.netloc and not url.query and not url.fragment
                        and path and not path.startswith('/') and '..' not in PurePosixPath(path).parts,
                        'Nonportable static link.')
                target = str(PurePosixPath(self.name).parent / path)
                require(target in SITE_FILES, 'Broken or unlisted static link.')


def source_inventory(root):
    found = set()
    for directory, dirs, files in os.walk(root, followlinks=False):
        directory = Path(directory)
        for name in list(dirs):
            path = directory/name
            require(not path.is_symlink(), 'Source symlinks are not accepted.')
            if name == '.git' or name == '__pycache__' or path == root/'_site':
                dirs.remove(name)
        for name in files:
            path = directory/name
            if path == root/'.git':  # Linked worktree metadata is never site content.
                continue
            require(not path.is_symlink(), 'Source symlinks are not accepted.')
            found.add(path.relative_to(root).as_posix())
    require(found == SOURCE_FILES, 'Missing or unexpected source file.')


def validate_source(root):
    root = Path(root)
    source_inventory(root)
    content = {name: (root/name).read_bytes() for name in SITE_FILES}
    public_text = {name: raw.decode('utf-8') for name, raw in content.items()}
    public_text['README.md'] = (root/'README.md').read_text(encoding='utf-8')
    for text in public_text.values():
        require(not PRIVATE.search(unescape(text)), 'Private metadata in public content.')
    provenance = json.loads(public_text['provenance.json'])
    keys(provenance, {'exporter_commit', 'snapshot_at', 'exported_at', 'artifacts'})
    require(isinstance(provenance['exporter_commit'], str)
            and re.fullmatch(r'[0-9a-f]{40}', provenance['exporter_commit']), 'Invalid exporter revision.')
    pair = set(SITE_FILES[2:])
    keys(provenance['artifacts'], pair)
    for name in pair:
        digest = provenance['artifacts'][name]
        require(isinstance(digest, str) and HEX.fullmatch(digest)
                and hashlib.sha256(content[name]).hexdigest() == digest, 'Reviewed artifact hash mismatch.')
    data = json.loads(public_text['trimui-smart-pro/hardware-status.json'])
    public_shape(data)
    public_strings(data)
    require(provenance['snapshot_at'] == data['snapshot_at']
            and provenance['exported_at'] == data['generated_at'], 'Provenance timestamp mismatch.')
    for name in ('index.html', 'README.md'):
        require(data['snapshot_at'] in public_text[name] and data['generated_at'] in public_text[name],
                'Landing-page or README dates are stale.')
    for name in SITE_FILES:
        if name.endswith('.html'):
            text = public_text[name]
            require(not re.search(r'@import|url\s*\(', text, re.I), 'External CSS resource.')
            StaticHTML(name).feed(text)
    return content


def build(root, output):
    root, output = Path(root), Path(output)
    require(not output.exists() and not output.is_symlink(), 'Choose a fresh output directory.')
    content = validate_source(root)  # Copy these validated bytes, never reopen source files.
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.site-build-', dir=output.parent) as temporary:
        stage = Path(temporary)
        for name, raw in content.items():
            destination = stage/name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
            destination.chmod(0o644)
        for directory in stage.rglob('*'):
            if directory.is_dir():
                directory.chmod(0o755)
        stage.chmod(0o755)
        stage.rename(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('_site'))
    args = parser.parse_args()
    try:
        build(Path(__file__).resolve().parents[1], args.output)
    except (ValueError, TypeError, KeyError, OSError):
        print('SITE_BUILD result=FAIL reason=invalid_source_or_output')
        return 2
    print('SITE_BUILD result=PASS files=4 external_resources=0')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
