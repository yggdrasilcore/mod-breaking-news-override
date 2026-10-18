# Module wiki

Static reader for GitHub Pages, generated from the module sources with
hosted-model prose. Per file, per function: signature, parameters, returns,
side effects, notes, plus a generated landing page.

- Generator: `docs/gen_wiki.py` (stdlib only)
- Template: `docs/wiki_template.html`, styles in `docs/vendor/yla-style.css`
- Cache: `docs/wiki-cache.json` (one entry per function signature + body hash)
- Reference: `docs/hints.md` + `docs/samples/` (author-provided ground truth)
- Output: `docs/dist/index.html`

The `wiki-docs` workflow runs on the default branch: reads the code, writes the
cache, deploys Pages. The cache is persisted with `actions/cache`, so nothing is
committed back.

## Ground truth

The model only sees this module's `src/` and `conf/`. If the behaviour depends on
something outside the repo (an external content file, a client-side format, a
runtime constraint), put it in `docs/hints.md` and/or a real sample under
`docs/samples/`. Both are fed to the landing-page prompt and folded into its
cache hash, so editing them regenerates the landing page.

## Usage

```bash
# scaffold only, no model needed
python3 docs/gen_wiki.py --root . --dry-run

# fill prose from a local model
python3 docs/gen_wiki.py --root . --model qwen3.8:4b

# only re-ask for files changed since a ref, reuse the rest from cache
python3 docs/gen_wiki.py --root . --model qwen3.8:4b --diff-ref HEAD~1
```

## Port to another module

Copy `docs/` into the target repo (generator, template, vendor, hints, samples)
and drop in `.github/workflows/wiki-docs.yml`. The module name, repo slug and
branch are derived from git, so no edits are required. Point `--scope` at the
source dir if it is not `src`.
