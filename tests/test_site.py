"""Exercise the real static build with broken-link and privacy controls."""
import hashlib
import json
import os
from pathlib import Path
import py_compile
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
source = ROOT/'scripts/build_site.py'
site = types.ModuleType('build_site')
site.__file__ = str(source)
exec(compile(source.read_bytes(), str(source), 'exec'), site.__dict__)


class SiteTest(unittest.TestCase):
    def fixture(self, parent):
        root=Path(parent)/'source'
        shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns('.git','_site','__pycache__'))
        return root

    def test_reviewed_source_and_explicit_output(self):
        source=site.validate_source(ROOT)
        self.assertEqual(set(source),set(site.SITE_FILES))
        data=json.loads(source['trimui-smart-pro/hardware-status.json'])
        self.assertEqual(len(data['hardware']),29)
        self.assertEqual(sum(len(g['rows']) for g in data['priorities']),32)
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)/'site';site.build(ROOT,out)
            files={str(p.relative_to(out)) for p in out.rglob('*') if p.is_file()}
            self.assertEqual(files,set(site.SITE_FILES))
            for name,raw in source.items():
                self.assertEqual((out/name).read_bytes(),raw)
                self.assertEqual(stat.S_IMODE((out/name).stat().st_mode),0o644)
            self.assertEqual(stat.S_IMODE(out.stat().st_mode),0o755)
            before={p:p.read_bytes() for p in out.rglob('*') if p.is_file()}
            with self.assertRaises(ValueError):site.build(ROOT,out)
            self.assertEqual(before,{p:p.read_bytes() for p in out.rglob('*') if p.is_file()})

    def test_extra_private_source_and_missing_source_refuse(self):
        for kind in ('extra','missing','symlink'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as td:
                root=self.fixture(td)
                if kind=='extra':(root/'private-input.json').write_text('{"private":"SYNTHETIC-CANARY"}')
                elif kind=='missing':(root/'README.md').unlink()
                else:(root/'unreviewed-link').symlink_to(root/'README.md')
                with self.assertRaises(ValueError):site.validate_source(root)

    def test_broken_external_and_traversal_links_refuse(self):
        for target in ('missing.html','https://example.invalid/','../private.json','/absolute.html'):
            with self.subTest(target=target),tempfile.TemporaryDirectory() as td:
                root=self.fixture(td);p=root/'index.html';p.write_text(p.read_text().replace('href="trimui-smart-pro/hardware-status.html"',f'href="{target}"'))
                with self.assertRaises(ValueError):site.validate_source(root)

    def test_pair_digest_mismatch_refuses(self):
        with tempfile.TemporaryDirectory() as td:
            root=self.fixture(td);p=root/'trimui-smart-pro/hardware-status.html';p.write_bytes(p.read_bytes()+b'changed')
            with self.assertRaises(ValueError):site.validate_source(root)

    def test_private_metadata_refuses_even_with_relocked_json(self):
        with tempfile.TemporaryDirectory() as td:
            root=self.fixture(td);p=root/'trimui-smart-pro/hardware-status.json';d=json.loads(p.read_bytes());d['lanes']=['SYNTHETIC-CANARY'];p.write_text(json.dumps(d));prov=json.loads((root/'provenance.json').read_bytes());prov['artifacts']['trimui-smart-pro/hardware-status.json']=hashlib.sha256(p.read_bytes()).hexdigest();(root/'provenance.json').write_text(json.dumps(prov))
            with self.assertRaises(ValueError):site.validate_source(root)

    def test_relocked_changed_snapshot_refuses(self):
        changes = [lambda d: d.update(input_sha256='not-a-digest'),
                   lambda d: d['hardware'][0].update(state='DefinitelyNotAStatus', record_age_days=-400,
                                                    evidence_digests=['not-a-digest']),
                   lambda d: d['hardware_counts'].update(Working=999)]
        for change in changes:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as td:
                root=self.fixture(td);p=root/'trimui-smart-pro/hardware-status.json'
                data=json.loads(p.read_bytes());change(data);p.write_text(json.dumps(data))
                prov=json.loads((root/'provenance.json').read_bytes())
                prov['artifacts']['trimui-smart-pro/hardware-status.json']=hashlib.sha256(p.read_bytes()).hexdigest()
                (root/'provenance.json').write_text(json.dumps(prov))
                with self.assertRaises(ValueError):site.validate_source(root)
        with tempfile.TemporaryDirectory() as td:
            root=self.fixture(td);p=root/'trimui-smart-pro/hardware-status.html'
            p.write_text(p.read_text().replace('29 components','999 components'))
            prov=json.loads((root/'provenance.json').read_bytes())
            prov['artifacts']['trimui-smart-pro/hardware-status.html']=hashlib.sha256(p.read_bytes()).hexdigest()
            (root/'provenance.json').write_text(json.dumps(prov))
            with self.assertRaises(ValueError):site.validate_source(root)
        with tempfile.TemporaryDirectory() as td:
            root=self.fixture(td);p=root/'provenance.json';prov=json.loads(p.read_bytes())
            prov['exporter_commit']='a'*40;p.write_text(json.dumps(prov))
            with self.assertRaises(ValueError):site.validate_source(root)

    def test_landing_and_readme_snapshot_facts_refuse(self):
        cases=[('index.html','<h2>TrimUI Smart Pro</h2>','<h2>Another device</h2>'),
               ('index.html','Allwinner A133 · PowerVR GE8300','Another chip · Another GPU'),
               ('index.html','Snapshot: 2026-10-29T20:06:32Z','Old: 2026-10-29T20:06:32Z'),
               ('README.md','29 hardware components and 32 product priorities','999 hardware components and 32 product priorities'),
               ('README.md','The current snapshot is','An unrelated snapshot is')]
        for name,before,after in cases:
            with self.subTest(name=name,change=after),tempfile.TemporaryDirectory() as td:
                root=self.fixture(td);p=root/name;p.write_text(p.read_text().replace(before,after))
                with self.assertRaises(ValueError):site.validate_source(root)

    def test_external_fetch_constructs_refuse(self):
        payloads=[r'<style>body{background:u\72l(https://example.invalid/a)}</style>',
                  '<style>body{background:image-set("https://example.invalid/a" 1x)}</style>',
                  r'<style>@\69mport "https://example.invalid/a";</style>',
                  '<svg><image xlink:href="https://example.invalid/a"/></svg>',
                  '<a href="provenance.json" ping="https://example.invalid/a">link</a>',
                  '<body background="https://example.invalid/a">',
                  '<p style="background:url(https://example.invalid/a)">text</p>']
        for payload in payloads:
            with self.subTest(payload=payload),tempfile.TemporaryDirectory() as td:
                root=self.fixture(td);p=root/'index.html';p.write_text(p.read_text()+payload)
                with self.assertRaises(ValueError):site.validate_source(root)

    def test_direct_suite_ignores_timestamp_valid_stale_bytecode(self):
        with tempfile.TemporaryDirectory() as td:
            root=self.fixture(td)
            for relative in ('tests/test_site.py','scripts/build_site.py'):
                path=root/relative;raw=path.read_bytes();stamp=path.stat().st_mtime_ns
                poison=b'raise RuntimeError("SYNTHETIC_STALE_BYTECODE")\n'
                path.write_bytes(poison+b' '*(len(raw)-len(poison)))
                os.utime(path,ns=(stamp,stamp));py_compile.compile(str(path),doraise=True)
                path.write_bytes(raw);os.utime(path,ns=(stamp,stamp))
            result=subprocess.run([sys.executable,str(root/'tests/test_site.py'),
                                   'SiteTest.test_reviewed_source_and_explicit_output'],
                                  capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_private_prose_and_active_resources_refuse(self):
        cases=[('README.md','/home/example/private'),('README.md','&#47;home&#47;example&#47;private'),('index.html','<script>alert(1)</script>'),
               ('index.html','<img src="https://example.invalid/track">'),('index.html','<style>@import "external.css";</style>')]
        for name,payload in cases:
            with self.subTest(payload=payload),tempfile.TemporaryDirectory() as td:
                root=self.fixture(td);p=root/name;p.write_text(p.read_text()+payload)
                with self.assertRaises(ValueError):site.validate_source(root)

    def test_invalid_source_never_creates_output(self):
        with tempfile.TemporaryDirectory() as td:
            root=self.fixture(td);(root/'private-input.json').write_text('SYNTHETIC-CANARY');out=Path(td)/'site'
            with self.assertRaises(ValueError):site.build(root,out)
            self.assertFalse(out.exists())


if __name__=='__main__':unittest.main()
