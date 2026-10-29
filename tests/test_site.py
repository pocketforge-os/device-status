"""Exercise the real static build with broken-link and privacy controls."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_site', ROOT/'scripts/build_site.py')
site = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site)


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
