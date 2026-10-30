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
# Release pins for the independently reviewed exporter output. Provenance alone
# cannot authorize different bytes. Updating these pins requires a reviewed export.
REVIEWED_PAIR = {
    'trimui-smart-pro/hardware-status.html': 'f27920e17d41da8131a82b0081409454155700e8a7e2c7786835e4cd5413eb57',
    'trimui-smart-pro/hardware-status.json': '76c5c4dc9ffef85b0ea766162f38d3643344f66deec29a811d537005f7d2e518',
}
# Reviewed editorial bytes are release pins, not a natural-language validator.
REVIEWED_PAGES = {
    'index.html': '5bc93fff854677ef2ea67ab2e4c86e0cfba7a794b2d52e1413f745032b714124',
    'README.md': '2edcaaca2a17d5d9ecff2465f38b1cdb26e9a727d578f75e4dd81488148a2bb5',
}
REVIEWED_EXPORTER = '8c876d18bf6846edd02b2faf34e68d88999e24e8'
SOURCE_FILES = set(SITE_FILES) | {'README.md', '.gitignore', 'scripts/build_site.py',
                                'tests/test_site.py', '.github/workflows/pages.yml'}
PRIVATE = re.compile(r'/(?:home|root|run|tmp|var)/|\.lan\b|\b(?:pft-|tsp-)|\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b|\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b')
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
    TAGS = {'html', 'head', 'meta', 'title', 'style', 'body', 'main', 'header', 'p',
            'h1', 'h2', 'h3', 'article', 'a', 'small', 'footer', 'section', 'aside',
            'strong', 'div', 'time', 'br', 'code', 'span'}

    def __init__(self, name):
        super().__init__(convert_charrefs=True)
        self.name = name
        self.in_style = False

    def handle_starttag(self, tag, attrs):
        require(tag in self.TAGS, 'Unlisted HTML tag.')
        allowed = {'html': {'lang'}, 'meta': {'charset', 'name', 'content'}, 'a': {'href'}}.get(tag, {'class'})
        require(len({k for k, _ in attrs}) == len(attrs), 'Duplicate HTML attribute.')
        if tag == 'meta':
            require(dict(attrs) in ({'charset': 'utf-8'},
                                   {'name': 'viewport', 'content': 'width=device-width, initial-scale=1'}),
                    'Unlisted HTML metadata.')
        for key, value in attrs:
            require(key in allowed, 'Unlisted HTML attribute.')
            if key == 'href':
                require(tag == 'a' and isinstance(value, str), 'Invalid static link.')
                url = urlsplit(value)
                path = unquote(url.path)
                require(not url.scheme and not url.netloc and not url.query and not url.fragment
                        and path and not path.startswith('/') and '..' not in PurePosixPath(path).parts,
                        'Nonportable static link.')
                target = str(PurePosixPath(self.name).parent / path)
                require(target in SITE_FILES, 'Broken or unlisted static link.')
        if tag == 'style':
            self.in_style = True

    def handle_endtag(self, tag):
        if tag == 'style':
            self.in_style = False

    def handle_data(self, data):
        if self.in_style:
            # This static layout needs no CSS strings, escapes, comments or
            # fetching functions. Reject those rather than normalize arbitrary CSS.
            require(data.isascii() and not any(x in data for x in ('\\', '"', "'", '/*', '*/')),
                    'Unlisted CSS syntax.')
            require('@' not in re.sub(r'@media(?=\s*\()', '', data, flags=re.I), 'Unlisted CSS rule.')
            functions = re.findall(r'([-\w]+)\s*\(', data)
            require(all(f.lower() in {'var', 'clamp', 'media'} for f in functions), 'Unlisted CSS function.')


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
    for name, digest in REVIEWED_PAGES.items():
        raw = (root/name).read_bytes() if name == 'README.md' else content[name]
        require(hashlib.sha256(raw).hexdigest() == digest, 'Reviewed landing-page or README hash mismatch.')
    public_text = {name: raw.decode('utf-8') for name, raw in content.items()}
    public_text['README.md'] = (root/'README.md').read_text(encoding='utf-8')
    for text in public_text.values():
        require(not PRIVATE.search(unescape(text)), 'Private metadata in public content.')
    provenance = json.loads(public_text['provenance.json'])
    keys(provenance, {'exporter_commit', 'snapshot_at', 'exported_at', 'artifacts'})
    require(provenance['exporter_commit'] == REVIEWED_EXPORTER, 'Reviewed exporter revision differs.')
    pair = set(SITE_FILES[2:])
    keys(provenance['artifacts'], pair)
    for name in pair:
        digest = provenance['artifacts'][name]
        require(digest == REVIEWED_PAIR[name]
                and hashlib.sha256(content[name]).hexdigest() == digest, 'Reviewed artifact hash mismatch.')
    data = json.loads(public_text['trimui-smart-pro/hardware-status.json'])
    public_shape(data)
    public_strings(data)
    require(provenance['snapshot_at'] == data['snapshot_at']
            and provenance['exported_at'] == data['generated_at'], 'Provenance timestamp mismatch.')
    for name in SITE_FILES:
        if name.endswith('.html'):
            text = public_text[name]
            parser = StaticHTML(name)
            parser.feed(text)
            parser.close()  # Flush EOF raw text (notably an unterminated style).
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
