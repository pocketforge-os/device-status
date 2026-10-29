# PocketForge OS device status

Portable, public snapshots of device hardware and product progress. Start with
[index.html](index.html) and the [TrimUI Smart Pro status](trimui-smart-pro/hardware-status.html).
The [JSON](trimui-smart-pro/hardware-status.json) and [provenance](provenance.json)
provide the same dated results and the reviewed artifact hashes.

The current snapshot is **2026-10-29T20:06:32Z**, exported at
**2026-10-29T20:31:39Z**. It covers 29 hardware components and 32 product priorities.
Functional downstream support comes first; upstreaming follows later. A Working
row describes only its recorded scope. Older builds, candidates and source-only
findings do not certify the latest image. Record dates can differ from original
test dates. Unpublished evidence details are intentionally left unpublished.

## Validate and build

Python 3.12 with its standard library is sufficient; there are no package installs,
remote assets, analytics, live queries or private service dependencies.

```sh
python3 -m unittest discover -s tests -v
python3 scripts/build_site.py --output _site
```

Use a fresh output directory for every build. The build validates source and
stages only the relative landing page, provenance and reviewed HTML/JSON pair.
The generated directory can be served by any ordinary static host or opened
locally. For another build, choose a new `--output` directory.

The GitHub Pages workflow is a replaceable hosting adapter: PR builds validate
with read-only repository permissions, and only a main-branch push may deploy.
Repository administrators configure and verify the actual publication. No live
URL is claimed by this README. Moving to Forgejo or another host does not change
the static content or data format.

## Updating a snapshot

Review the exported pair and its privacy boundary before a normal update PR.
Keep the pair unchanged, update the two hashes and timestamps in provenance,
and update the landing page/README dates. Do not copy operational snapshots,
private receipts, commands, identifiers or unrelated files into this repository.
CI checks the file allowlist, hashes, public JSON shape, static links and absence
of private metadata or active/external resources. These checks support privacy
review; they do not replace inspection of new public prose. Publishing a snapshot does not
turn a partial result into complete device acceptance.
