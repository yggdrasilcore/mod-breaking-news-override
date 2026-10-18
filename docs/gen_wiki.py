#!/usr/bin/env python3
"""C++ module wiki generator: source files -> hosted-model prose -> static Pages wiki.

Successor to yggdrasilcore's docs/gen_ai.py, re-shaped from a class/method API
browser into a per-file / per-function wiki:

- Extracts classes and functions from --scope dirs (default: src), uniting
  header declarations with their .cpp definitions, and keeps the source body
  that grounds each prompt.
- Each entry gets a full-fledged prose page: summary, overview, parameters,
  returns, side effects and notes.
- Caches per function signature + body hash in docs/wiki-cache.json; only
  stale/missing entries hit the model. A `--diff-ref` pass only re-asks for
  changed files and reuses every cached page for the rest.
- Builds a hierarchical static reader (docs/dist) for GitHub Pages.

Providers (single OpenAI-compatible path):
- Local Ollama (default):   python3 docs/gen_wiki.py --model qwen3.8:4b
- OpenAI-compatible host:   GITHUB_TOKEN=... python3 docs/gen_wiki.py --endpoint ... --model ...

Scaffold only, no model needed:  python3 docs/gen_wiki.py --dry-run
"""

import argparse
import datetime
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SCOPE = ["src"]
DEFAULT_MODEL = "qwen3.8:4b"

CONTROL = {
    "if", "for", "while", "switch", "catch", "do", "else", "return",
    "sizeof", "new", "delete", "throw", "case",
}
CLASS_RE = re.compile(
    r"^\s*(?:template\s*<[^>]*>\s*)?(class|struct)\s+([A-Za-z_]\w*)\s*(?::\s*([^{]+?))?\s*$"
)
NAME_RE = re.compile(r"(operator\s*[^\s(]+|[~A-Za-z_][\w:]*)\s*$")


def preprocess(text):
    """Drop comments and preprocessor lines; keep newline count for line numbers."""
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                out.append("\n" if text[i] == "\n" else " ")
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    lines = out = "".join(out).split("\n")
    return "\n".join("" if ln.lstrip().startswith("#") else ln for ln in lines)


def skip_string(text, i):
    quote = text[i]
    i += 1
    n = len(text)
    while i < n:
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            return i + 1
        i += 1
    return i


def match_brace(text, start):
    """String-aware balanced-brace slice beginning at text[start] == '{'."""
    depth = 0
    i, n = start, len(text)
    while i < n:
        c = text[i]
        if c == '"' or c == "'":
            i = skip_string(text, i)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1], i + 1
        i += 1
    return text[start:], n


def split_args(args):
    parts, depth, buf, i, n = [], 0, "", 0, len(args)
    while i < n:
        c = args[i]
        if c in "<([{":
            depth += 1
        elif c in ">)]}":
            depth -= 1
        if c == "," and depth == 0:
            parts.append(buf.strip())
            buf = ""
        else:
            buf += c
        i += 1
    if buf.strip():
        parts.append(buf.strip())
    return [p for p in parts if p]


def parse_params(args):
    out = []
    for raw in split_args(args):
        raw = re.sub(r"=[^=]*$", "", raw).strip()
        if not raw or raw == "void" or raw.endswith("...") and " " not in raw:
            continue
        tokens = re.sub(r"([*&])", r" \1 ", raw).split()
        if len(tokens) >= 2 and re.fullmatch(r"[A-Za-z_]\w*", tokens[-1]):
            name, typ = tokens[-1], " ".join(tokens[:-1])
        else:
            name, typ = "", raw
        out.append({"name": name, "type": typ.strip(), "raw": raw})
    return out


def parse_header(header):
    """Return (kind, name, ret, params_str, qualifier) for a statement, else None."""
    header = re.sub(r"^\s*(?:public|protected|private)\s*:\s*", "", header.strip())
    header = re.sub(r"^\s*(?:template\s*<[^>]*>\s*)?", "", header).strip()
    if "=" in header or header.startswith(("using ", "typedef ", "return ", "enum ")):
        return None
    close = header.rfind(")")
    if close == -1:
        return None
    depth, open_i = 0, -1
    for i in range(close, -1, -1):
        if header[i] == ")":
            depth += 1
        elif header[i] == "(":
            depth -= 1
            if depth == 0:
                open_i = i
                break
    if open_i == -1:
        return None
    name_part = header[:open_i].strip()
    args = header[open_i + 1 : close].strip()
    m = NAME_RE.search(name_part)
    if not m:
        return None
    full = m.group(1)
    ret = name_part[: m.start()].strip()
    qualifier, name = (full.rsplit("::", 1) + [""])[:2] if "::" in full else ("", full)
    if name in CONTROL or full in CONTROL or ret.endswith("<"):
        return None
    if ret.split()[-1:] and ret.split()[-1] in CONTROL:
        return None
    return name, ret, args, qualifier


def scan_file(path):
    """Yield class and function entries found in a single translation unit."""
    try:
        with open(path, errors="ignore") as f:
            text = preprocess(f.read())
    except OSError:
        return []
    out, i, n, line = [], 0, len(text), 1
    stmt, stmt_line, closers = [], 1, []
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            if stmt:
                stmt.append(c)
            i += 1
            continue
        if not stmt:
            if c.isspace():
                i += 1
                continue
            stmt_line = line
        if c == '"' or c == "'":
            j = skip_string(text, i)
            stmt.append(text[i:j])
            line += text[i:j].count("\n")
            i = j
            continue
        if c == "{":
            header = "".join(stmt).strip()
            cm = CLASS_RE.match(header)
            body, end = match_brace(text, i)
            if cm and cm.group(2) not in CONTROL:
                bases = [
                    b.strip().split()[-1]
                    for b in (cm.group(3) or "").split(",")
                    if b.strip()
                ]
                out.append({
                    "kind": "class",
                    "name": cm.group(2),
                    "qualifier": "",
                    "ret": cm.group(1),
                    "sig": re.sub(r"\s+", " ", header),
                    "params": [],
                    "bases": bases,
                    "line": stmt_line,
                    "body": body,
                })
            else:
                parsed = parse_header(header) if "(" in header else None
                if parsed:
                    name, ret, args, qualifier = parsed
                    out.append({
                        "kind": "function",
                        "name": name,
                        "qualifier": qualifier,
                        "ret": ret,
                        "sig": "%s(%s)" % ("%s %s" % (ret, name) if ret else name, args),
                        "params": parse_params(args),
                        "bases": [],
                        "line": stmt_line,
                        "body": body,
                    })
            stmt = []
            line += text[i:end].count("\n")
            i = end
            continue
        if c == "}":
            stmt = []
            i += 1
            continue
        if c == ";":
            header = "".join(stmt).strip()
            if "(" in header:
                parsed = parse_header(header)
                if parsed:
                    name, ret, args, qualifier = parsed
                    out.append({
                        "kind": "function",
                        "name": name,
                        "qualifier": qualifier,
                        "ret": ret,
                        "sig": "%s(%s);" % ("%s %s" % (ret, name) if ret else name, args),
                        "params": parse_params(args),
                        "bases": [],
                        "line": stmt_line,
                        "body": "",
                    })
            stmt = []
            i += 1
            continue
        if not stmt:
            stmt_line = line
        stmt.append(c)
        i += 1
    return out


def find_sources(scope_dirs):
    files = []
    for d in scope_dirs:
        base = os.path.join(ROOT, d)
        for dp, _, fns in os.walk(base):
            for fn in fns:
                if fn.endswith((".h", ".hpp", ".cpp", ".cc")):
                    files.append(os.path.join(dp, fn))
    return sorted(files)


def collect_entries():
    """Scan every source file; merge header declarations into cpp definitions."""
    files = find_sources(SCOPE)
    per_file, defined = {}, set()
    for path in files:
        rel = os.path.relpath(path, ROOT)
        syms = scan_file(path)
        per_file[rel] = syms
        for s in syms:
            if s["kind"] == "function" and s["body"]:
                defined.add(s["name"])

    entries = []
    for rel in sorted(per_file):
        kept = []
        for s in per_file[rel]:
            if s["kind"] == "function" and not s["body"] and s["name"] in defined:
                continue  # header declaration already covered by its .cpp definition
            kept.append(s)
        if kept:
            entries.append({"path": rel, "entries": kept})
    return entries


def body_hash(sym):
    h = hashlib.sha1()
    h.update((sym["sig"] + "\n" + sym["body"]).encode("utf-8", "ignore"))
    return h.hexdigest()[:12]


THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
SYSTEM_PROMPT = (
    "You are a wiki author for a C++ game-server module. Reply with a single JSON "
    "object only, no markdown fences."
)
USER_TMPL = (
    "Write a wiki page for this C++ {kind} as JSON with keys "
    '"summary" (one sentence), "overview" (2-4 sentence paragraph), '
    '"params" (array with one {{"name","desc"}} object for every parameter listed '
    'below, [] if none), "returns" (or "void"), '
    '"side_effects" (locks, I/O, network, global state; "None" if none), '
    "and \"notes\" (callers, edge cases, or \"\").\n"
    "Parameters to document: {params}\n"
    "File: {file}\nSignature: {sig}\nDefinition:\n{body}"
)
SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "overview": {"type": "string"},
        "params": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "desc": {"type": "string"}},
                "required": ["name", "desc"],
            },
        },
        "returns": {"type": "string"},
        "side_effects": {"type": "string"},
        "notes": {"type": "string"},
    },
    "required": ["summary", "overview", "params", "returns", "side_effects"],
}
ECHO_RE = re.compile(
    r"one sentence|the task\b|we need to|we are to|output only|no preamble|"
    r"as an ai|i cannot|signature is|the above code",
    re.IGNORECASE,
)


def _post(url, payload, timeout=300):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def sanitize(s):
    return re.sub(r"\s+", " ", THINK_RE.sub("", s or "")).strip()


def valid_doc(doc, sig):
    summary = sanitize(doc.get("summary"))
    overview = sanitize(doc.get("overview"))
    if not (12 <= len(summary) <= 300) or not (20 <= len(overview) <= 1400):
        return None
    if ECHO_RE.search(summary):
        return None
    if difflib.SequenceMatcher(None, summary.lower(), sig.lower()).ratio() > 0.6:
        return None
    params = [
        {"name": sanitize(p.get("name", "")), "desc": sanitize(p.get("desc", ""))}
        for p in (doc.get("params") or [])
        if isinstance(p, dict) and p.get("name")
    ]
    return {
        "summary": summary,
        "overview": overview,
        "params": params,
        "returns": sanitize(doc.get("returns")) or "void",
        "side_effects": sanitize(doc.get("side_effects")) or "None",
        "notes": sanitize(doc.get("notes")),
    }


def ollama_describe(endpoint, model, sym, rel):
    """(doc, raw). Native Ollama chat, thinking off, JSON schema, one retry."""
    url = endpoint.rstrip("/") + "/api/chat"
    base = {
        "model": model,
        "stream": False,
        "think": False,
        "format": SCHEMA,
        "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 700},
    }
    content = USER_TMPL.format(
        kind=sym["kind"],
        file=rel,
        sig=sym["sig"],
        params=", ".join(p["name"] for p in sym["params"] if p["name"]) or "(none)",
        body=sym["body"][:5000],
    )
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}]
    for attempt in range(2):
        try:
            payload = dict(base, messages=list(msgs))
            if attempt == 1:
                payload["messages"].append(
                    {"role": "user", "content": "Too vague. Name concrete actions, calls and effects."}
                )
            data = _post(url, payload)
            raw = (data.get("message", {}).get("content") or "").strip()
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            doc = valid_doc(obj, sym["sig"])
            if doc:
                return doc, raw
        except Exception as e:  # never fail the job
            print("WARN llm failed: %s" % e, file=sys.stderr)
            return None, ""
    return None, ""


def placeholder_doc(sym):
    return {
        "summary": "Awaiting generation by %s." % MODEL,
        "overview": (
            "This page is scaffolded from the source. Run gen_wiki.py against a live "
            "%s endpoint to fill in the prose." % MODEL
        ),
        "params": [{"name": p["name"], "desc": "(pending)"} for p in sym["params"] if p["name"]],
        "returns": "(pending)",
        "side_effects": "(pending)",
        "notes": "",
        "ai": False,
    }


OVERVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "body": {"type": "string"},
    },
    "required": ["summary", "body"],
}
OVERVIEW_TMPL = (
    "Write the landing page for the {module} C++ module wiki as a qualitative text. "
    "In `body`, use markdown ('## Heading', '- ' bullets, `code`, **bold**, and "
    "fenced ``` code blocks) and cover:\n"
    "1. What the module does overall.\n"
    "2. How it works (the moving parts and the flow between them).\n"
    "3. The key functions/classes and what each one does.\n"
    "4. How a server operator or developer uses it themselves (config keys, hooks).\n"
    "When you list commands, options, strategies or configuration, prefer a markdown "
    "table ('| column | column |' followed by a '| --- | --- |' separator row).\n"
    "5. A '## Usage example' section with a LITERAL, copy-paste example: one fenced "
    "block showing the config options, and one fenced block showing the exact "
    "content file the module injects. Use the EXACT option names, defaults and value "
    "format from the configuration file in Sources, and MATCH the markup subset and "
    "style shown in the REFERENCE files (do not use unsupported tags, CSS or scripts; "
    "do not invent options or sections).\n"
    'Reply as JSON {{"summary": "<=2 sentences", "body": "markdown"}} only.\n'
    "Sources:\n{ctx}"
)

# Bump when prompts change, so cached prose is invalidated instead of reused.
PROMPT_VERSION = "5"


def conf_files():
    out = []
    for dp, _, fns in os.walk(os.path.join(ROOT, "conf")):
        for fn in fns:
            if fn.endswith((".conf", ".conf.dist")):
                out.append(os.path.join(dp, fn))
    return sorted(out)


def reference_files():
    """Human-authored ground truth fed to the model: real content samples and notes.

    The injected content file lives outside this repo, so the source alone cannot
    teach the model the allowed markup. Put a real sample in docs/samples/ and
    constraints in docs/hints.md.
    """
    out = []
    base = os.path.join(ROOT, "docs", "samples")
    if os.path.isdir(base):
        for dp, _, fns in os.walk(base):
            out.extend(os.path.join(dp, fn) for fn in sorted(fns))
    hints = os.path.join(ROOT, "docs", "hints.md")
    if os.path.isfile(hints):
        out.append(hints)
    return sorted(out)


def overview_hash(tree):
    h = hashlib.sha1(PROMPT_VERSION.encode())
    for f in tree:
        h.update(f["path"].encode())
        for e in f["entries"]:
            h.update((e["name"] + e["sig"] + e.get("hash", "")).encode("utf-8", "ignore"))
    for extra in conf_files() + reference_files():
        h.update(os.path.relpath(extra, ROOT).encode())
        try:
            with open(extra, errors="ignore") as fh:
                h.update(fh.read().encode("utf-8", "ignore"))
        except OSError:
            pass
    return h.hexdigest()[:12]


def overview_context(tree, per_budget=400, total=18000):
    out = []
    for f in tree:
        out.append("### %s" % f["path"])
        for e in f["entries"]:
            label = ("%s::" % e["qualifier"] if e["qualifier"] else "") + e["name"]
            out.append("- %s %s — %s" % (e["kind"], label, e["sig"]))
            body = re.sub(r"\s+", " ", e["body"]).strip()
            if body:
                out.append("    %s" % body[:per_budget])
    for cf in conf_files():
        try:
            with open(cf, errors="ignore") as fh:
                body = fh.read().strip()
        except OSError:
            continue
        out.append("\n### CONFIG FILE: %s\n%s" % (os.path.relpath(cf, ROOT), body[:4000]))
    for rf in reference_files():
        try:
            with open(rf, errors="ignore") as fh:
                body = fh.read().strip()
        except OSError:
            continue
        out.append("\n### REFERENCE: %s\n%s" % (os.path.relpath(rf, ROOT), body[:4000]))
    return "\n".join(out)[:total]


def valid_overview(obj):
    summary = sanitize(obj.get("summary"))
    body = (obj.get("body") or "").strip()
    if not (12 <= len(summary) <= 400) or len(body) < 80:
        return None
    if ECHO_RE.search(body[:300]):
        return None
    return {"summary": summary, "body": body}


def ollama_overview(endpoint, model, ctx):
    url = endpoint.rstrip("/") + "/api/chat"
    base = {
        "model": model,
        "stream": False,
        "think": False,
        "format": OVERVIEW_SCHEMA,
        "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 1600},
    }
    content = OVERVIEW_TMPL.format(module=MODULE, ctx=ctx)
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}]
    for attempt in range(2):
        try:
            payload = dict(base, messages=list(msgs))
            if attempt == 1:
                payload["messages"].append(
                    {"role": "user", "content": "Too vague. Name concrete functions and config keys."}
                )
            data = _post(url, payload)
            raw = (data.get("message", {}).get("content") or "").strip()
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            doc = valid_overview(obj)
            if doc:
                return doc, raw
        except Exception as e:  # never fail the job
            print("WARN llm failed: %s" % e, file=sys.stderr)
            return None, ""
    return None, ""


def overview_placeholder():
    return {
        "summary": "Awaiting generation by %s." % MODEL,
        "body": (
            "The landing page is written by `%s` from the module sources. Run "
            "gen_wiki.py without `--dry-run` to fill it in.\n\n"
            "## What it does\n(pending)\n\n## How it works\n(pending)\n\n"
            "## Key functions\n(pending)\n\n## Using it yourself\n(pending)" % MODEL
        ),
        "ai": False,
    }


def build_reader(entries, overview, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    payload = {
        "module": MODULE,
        "model": MODEL,
        "generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "overview": overview,
        "files": entries,
    }
    with open(os.path.join(out_dir, "wiki.json"), "w") as f:
        json.dump(payload, f, indent=1)
    for asset in ("logo.png", "favicon.ico", "favicon.png"):
        src = os.path.join(os.path.dirname(os.path.abspath(__file__)), asset)
        if os.path.isfile(src):
            with open(src, "rb") as fs, open(os.path.join(out_dir, asset), "wb") as fd:
                fd.write(fs.read())
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "wiki_template.html")) as f:
        html = f.read()
    with open(os.path.join(here, "vendor", "yla-style.css")) as f:
        html = html.replace("YLA_CSS_PLACEHOLDER", f.read())
    html = html.replace("WIKI_PLACEHOLDER", json.dumps(payload))
    html = html.replace("FOOT_MODEL", MODEL)
    html = html.replace("MODULE_LABEL", "%s — module wiki" % MODULE)
    html = html.replace("MODULE_NAME", MODULE)
    html = html.replace("FOOT_GENERATED", payload["generated"])
    html = html.replace("SRC_BASE_PLACEHOLDER", SRC_BASE)
    html = html.replace("FOOT_YEAR", str(datetime.date.today().year))
    with open(os.path.join(out_dir, "index.html"), "w") as f:
        f.write(html)


def key_for(rel, sym):
    sigh = hashlib.sha1(sym["sig"].encode()).hexdigest()[:8]
    return "%s::%s@%s:%s" % (rel, sym["name"], sigh, re.sub(r"\W+", "-", MODEL))


def git_diff_files(ref):
    try:
        out = subprocess.check_output(["git", "diff", "--name-only", ref], cwd=ROOT, text=True)
        return set(out.split())
    except subprocess.CalledProcessError:
        return set()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", nargs="*", default=DEFAULT_SCOPE)
    ap.add_argument("--root", default=None)
    ap.add_argument("--cache", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--endpoint", default="http://localhost:11434")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--diff-ref", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    global ROOT, SCOPE, MODEL, MODULE, SRC_BASE
    if a.root:
        ROOT = os.path.abspath(a.root)
    SCOPE, MODEL = a.scope, a.model
    MODULE = os.path.basename(ROOT) or "module"
    try:
        remote = subprocess.check_output(["git", "remote", "get-url", "origin"], cwd=ROOT, text=True).strip()
        slug = re.sub(r"^git@github\.com:", "", remote)
        slug = re.sub(r"^https://github\.com/", "", slug).removesuffix(".git")
        branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT, text=True).strip()
        SRC_BASE = "https://github.com/%s/blob/%s/" % (slug, branch)
    except Exception:
        SRC_BASE = ""
    if not a.cache:
        a.cache = os.path.join(ROOT, "docs", "wiki-cache.json")
    if not a.out:
        a.out = os.path.join(ROOT, "docs", "dist")

    print("module: %s  model: %s @ %s" % (MODULE, MODEL, a.endpoint), flush=True)

    tree = collect_entries()
    diff_files = git_diff_files(a.diff_ref) if a.diff_ref else set()

    try:
        with open(a.cache) as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}

    done = skipped = failed = 0
    count = sum(len(f["entries"]) for f in tree)

    ohash = overview_hash(tree)
    okey = "__overview__@%s:%s" % (ohash, re.sub(r"\W+", "-", MODEL))
    ohit = cache.get(okey)
    if ohit and ohit.get("hash") == ohash and ohit.get("model") == MODEL and ohit.get("doc"):
        overview = ohit["doc"]
    elif a.dry_run:
        overview = overview_placeholder()
    else:
        odoc, oraw = ollama_overview(a.endpoint, MODEL, overview_context(tree))
        if odoc:
            overview = dict(odoc, ai=True)
            cache[okey] = {"hash": ohash, "doc": overview, "raw": oraw, "model": MODEL}
        else:
            overview = overview_placeholder()

    for file in tree:
        for sym in file["entries"]:
            rel = file["path"]
            sym["file"] = rel
            sym["hash"] = body_hash(sym)
            key = key_for(rel, sym)
            hit = cache.get(key)
            if (
                hit
                and hit.get("hash") == sym["hash"]
                and hit.get("doc") is not None
                and hit.get("model") == MODEL
            ):
                sym["doc"] = hit["doc"]
                skipped += 1
                continue
            if diff_files and rel not in diff_files:
                sym["doc"] = placeholder_doc(sym)
                skipped += 1
                continue
            if a.limit and done >= a.limit:
                sym["doc"] = placeholder_doc(sym)
                continue
            if a.dry_run:
                sym["doc"] = placeholder_doc(sym)
                sym["doc"].update(ai=False)
                continue
            if not sym["body"]:
                sym["doc"] = placeholder_doc(sym)
                continue
            doc, raw = ollama_describe(a.endpoint, MODEL, sym, rel)
            if doc:
                sym["doc"] = dict(doc, ai=True)
                cache[key] = {"hash": sym["hash"], "doc": sym["doc"], "raw": raw, "model": MODEL}
                done += 1
                if done % 10 == 0:
                    with open(a.cache, "w") as f:
                        json.dump(cache, f, indent=1)
                    build_reader(tree, overview, a.out)
                    print("progress: new=%d cached=%d failed=%d" % (done, skipped, failed), flush=True)
            else:
                sym["doc"] = placeholder_doc(sym)
                failed += 1

    if not a.dry_run:
        with open(a.cache, "w") as f:
            json.dump(cache, f, indent=1)
    build_reader(tree, overview, a.out)
    print(
        "files=%d entries=%d new=%d cached=%d failed=%d"
        % (len(tree), count, done, skipped, failed)
    )


if __name__ == "__main__":
    main()
