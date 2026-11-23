#!/usr/bin/env python3
"""The paper library: what an entry could not open, and what Miguel supplied.

    tools/papers.py have <doi>                     is it in the library? prints the paths
    tools/papers.py find <doi>                     look for an open copy and file it
    tools/papers.py want <doi> --entry <id> --title "..." --why "one line"
                                                   look for an open copy; failing that, ask
                                                   Miguel for it (at most three per entry)
    tools/papers.py file                           file what was dropped in data/papers/inbox/
    tools/papers.py list [--entry <id>] [--wanted] the library, or the open requests
    tools/papers.py assign <key or file> <doi>     correct what a filed PDF was taken for

Publishers refuse automated downloads, so a session that cannot open a paper registers
it with `want`. That first looks for a copy that is open to everybody (the locations
OpenAlex, Semantic Scholar and Europe PMC list: repositories, arXiv, PubMed Central) and
files it if one downloads. Otherwise the request reaches Miguel with the candidate on Discord; he replies with
the PDF, or drops it in data/papers/inbox/ under any name. A PDF is recognised by the DOI
printed in it, then by its file name, then by the words of the requested title.

    data/papers/inbox/       drop PDFs here
    data/papers/pdf/         the library, one file per paper, named after the DOI
    data/papers/text/        the text of each, from pdftotext (empty for a scan)
    data/papers/index.json   requests and holdings
    data/papers/rejected/    what was dropped in the inbox and is not a PDF

The PDFs are under copyright: they stay in data/papers/, which git ignores, and are
never copied into an entry or sent anywhere. Only the standard library is used, so that
auto/mad.py can import this file.
"""
import argparse, contextlib, datetime, fcntl, json, os, pathlib, re, shutil, subprocess, sys, time
import socket, urllib.parse, urllib.request

ROOT = pathlib.Path(os.environ.get("MAD_ROOT") or pathlib.Path(__file__).resolve().parent.parent)
DIR = ROOT / "data" / "papers"
INBOX, PDF, TEXT, INDEX = DIR / "inbox", DIR / "pdf", DIR / "text", DIR / "index.json"
MAX_WANTED = 3
UA = "material-a-day/1.0 (https://github.com/hyllios/material_a_day)"
DOI = re.compile(r"10\.\d{4,9}/[^\s\"<>{}|\\^`]+", re.I)


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def key_of(ref):
    """A DOI in its bare lower-case form; anything else (a URL, a citation) as given."""
    ref = ref.strip()
    m = DOI.search(ref)
    return m.group(0).rstrip(".,;)]").lower() if m else ref


def slug(key):
    return re.sub(r"[^A-Za-z0-9.-]+", "_", key).strip("_")[:120]


def rel(path):
    return str(pathlib.Path(path).relative_to(ROOT))


@contextlib.contextmanager
def index():
    """The index, read and written under a lock."""
    for d in (INBOX, PDF, TEXT):
        d.mkdir(parents=True, exist_ok=True)
    with open(DIR / "index.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ix = json.loads(INDEX.read_text()) if INDEX.exists() else {}
        yield ix
        tmp = INDEX.with_suffix(".tmp")
        tmp.write_text(json.dumps(ix, indent=1, ensure_ascii=False))
        os.replace(tmp, INDEX)


def entries():
    with index() as ix:
        return json.loads(json.dumps(ix))


def blank(key):
    return {"key": key, "title": "", "why": "", "wanted_by": [], "asked": [], "used_by": [],
            "file": None, "text": None}


# ---------------------------------------------------------------- requests

def have(ref):
    e = entries().get(key_of(ref))
    return e if e and e.get("file") and (ROOT / e["file"]).exists() else None


def want(ref, entry, title="", why="", search=True, limit=MAX_WANTED):
    """Register a request, unless an open copy can be had. Returns (the index entry, a message)."""
    key = key_of(ref)
    if search and not have(key):
        e = find(key)
        if e:
            with index() as ix:
                ix[key]["title"], ix[key]["why"] = title or ix[key]["title"], why or ix[key]["why"]
            return e, f"found an open copy, read it now: {e['file']} (from {e['source']})"
    with index() as ix:
        e = ix.setdefault(key, blank(key))
        if e.get("file"):
            return e, f"in the library already: {e['file']}"
        if entry not in e["wanted_by"]:
            n = sum(1 for o in ix.values() if entry in o["wanted_by"] and not o.get("file"))
            if n >= limit:
                if not e["wanted_by"]:
                    del ix[key]
                return None, f"{entry} has {limit} open requests already; ask only for what would change the entry"
            e["wanted_by"].append(entry)
            e.setdefault("requested", now())
        e["title"], e["why"] = title or e["title"], why or e["why"]
        return e, "requested"


def wanted(entry=None, unasked=False):
    """Open requests, for one entry or for all."""
    out = [e for e in entries().values() if not e.get("file") and e["wanted_by"]
           and (entry is None or entry in e["wanted_by"])
           and not (unasked and entry in e["asked"])]
    return sorted(out, key=lambda e: e.get("requested", ""))


def mark(keys, entry, field):
    """Note that these papers were asked for (`asked`) or read (`used_by`) for an entry."""
    with index() as ix:
        for k in keys:
            if k in ix and entry not in ix[k][field]:
                ix[k][field].append(entry)


def rename(old, new):
    """An entry got its final date: follow it."""
    with index() as ix:
        for e in ix.values():
            for field in ("wanted_by", "asked", "used_by"):
                e[field] = [new if x == old else x for x in e[field]]


# ---------------------------------------------------------------- open copies

def ipv4(host, port, family=0, *a, **k):
    """Name lookup over IPv4 only: the IPv6 route of this machine times out."""
    return _getaddrinfo(host, port, socket.AF_INET, *a, **k)


_getaddrinfo, socket.getaddrinfo = socket.getaddrinfo, ipv4


def get(url, limit=80e6, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/pdf, application/json, */*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(int(limit))


def open_locations(doi):
    """Addresses where a copy open to everybody is said to be, best guesses first."""
    urls, q = [], urllib.parse.quote(doi, safe="/")

    def add(u):
        if u and u not in urls:
            urls.append(u)
    with contextlib.suppress(Exception):
        w = json.loads(get(f"https://api.openalex.org/works/doi:{q}"))
        locs = [w.get("best_oa_location") or {}] + (w.get("locations") or [])
        for loc in locs:
            if loc.get("is_oa"):
                add(loc.get("pdf_url"))
        add((w.get("open_access") or {}).get("oa_url"))
        for loc in locs:
            if loc.get("is_oa"):
                add(loc.get("landing_page_url"))
    with contextlib.suppress(Exception):
        w = json.loads(get(f"https://api.semanticscholar.org/graph/v1/paper/DOI:{q}?fields=openAccessPdf,externalIds"))
        add((w.get("openAccessPdf") or {}).get("url"))
        if (w.get("externalIds") or {}).get("ArXiv"):
            add("https://arxiv.org/pdf/" + w["externalIds"]["ArXiv"])
    with contextlib.suppress(Exception):
        w = json.loads(get("https://www.ebi.ac.uk/europepmc/webservices/rest/search?format=json&resultType=core&query="
                           + urllib.parse.quote(f'DOI:"{doi}"')))
        for r in w.get("resultList", {}).get("result", []):
            if r.get("pmcid"):
                add("pmc:" + r["pmcid"])
            for u in (r.get("fullTextUrlList") or {}).get("fullTextUrl", []):
                if u.get("availabilityCode") in ("OA", "F") and u.get("documentStyle") == "pdf":
                    add(u.get("url"))
    if doi.startswith("10.48550/arxiv."):
        add("https://arxiv.org/pdf/" + doi.split("arxiv.", 1)[1])
    return urls


def fetch(url):
    """The PDF at an address, or behind the PDF link that a repository page declares."""
    if url.startswith("pmc:"):      # the full text as XML, by the service Europe PMC provides for programs
        return get(f"https://www.ebi.ac.uk/europepmc/webservices/rest/{url[4:]}/fullTextXML")
    data = get(url)
    if b"%PDF" not in data[:1024]:
        m = re.search(rb'<meta[^>]+name="citation_pdf_url"[^>]+content="([^"]+)"', data) or \
            re.search(rb'<meta[^>]+content="([^"]+)"[^>]+name="citation_pdf_url"', data)
        if m:
            data = get(urllib.parse.urljoin(url, m.group(1).decode().replace("&amp;", "&")))
    return data


def find(ref, verbose=False):
    """Download an open copy of a paper into the library. Returns its index entry or None.
    The request says what it is; a site that refuses it is left alone."""
    key = key_of(ref)
    if not key.startswith("10."):
        return None
    (DIR / "incoming").mkdir(parents=True, exist_ok=True)
    for url in open_locations(key)[:8]:
        try:
            data = fetch(url)
        except Exception as e:
            data = b""
            if verbose:
                print(f"  {url}: {type(e).__name__} {getattr(e, 'code', '')}")
        if b"%PDF" in data[:1024]:
            tmp = DIR / "incoming" / (slug(key) + ".pdf")
            tmp.write_bytes(data)
            return file_one(tmp, source=url, key=key, how="its address: an open copy listed for this DOI")
        if url.startswith("pmc:") and b"<article" in data[:2000]:
            return file_xml(key, data, "Europe PMC, " + url[4:])
        if verbose and data:
            print(f"  {url}: not a PDF")
    return None


def file_xml(key, data, source):
    """File a full text that came as XML: the words and tables, without the figures."""
    xml = data.decode(errors="replace")
    text = re.sub(r"[ \t]+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"</(p|title|tr|sec|caption)>", "\n", xml)))
    with index() as ix:
        e = ix.setdefault(key, blank(key))
        (PDF / (slug(key) + ".xml")).write_text(xml)
        (TEXT / (slug(key) + ".txt")).write_text(text)
        e.update(file=rel(PDF / (slug(key) + ".xml")), text=rel(TEXT / (slug(key) + ".txt")),
                 text_chars=len(text.strip()), received=now(), source=source, original_name=None,
                 identified_by="its address: the open full text for this DOI (XML, no figures)")
        return json.loads(json.dumps(e))


# ---------------------------------------------------------------- filing

def pdftotext(path, last=None):
    cmd = ["pdftotext", "-layout"] + (["-l", str(last)] if last else []) + [str(path), "-"]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=300).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def words(text):
    return {w for w in re.findall(r"[a-z0-9]{4,}", text.lower())}


def identify(path, name, ix, hint=None):
    """(key, how it was recognised) for a PDF, against the open requests first."""
    head = pdftotext(path, last=3)
    found = [m.rstrip(".,;)]").lower() for m in DOI.findall(head)]
    opened = [e for e in ix.values() if not e.get("file") and e["wanted_by"]]
    mine = [e for e in opened if hint in e["wanted_by"]] if hint else []
    for d in found:
        if d in ix:
            return d, "the DOI printed in the PDF"
    bare = lambda t: re.sub(r"[^a-z0-9]", "", t.lower())        # publishers name files after the DOI suffix
    for e in opened:
        tail = bare(e["key"].split("/", 1)[-1])
        if len(tail) >= 8 and tail in bare(re.sub(r"[_%]2[89]", "", name)):   # _28 and _29 are brackets
            return e["key"], "the file name"
    best, score = None, 0.0
    for e in mine + [e for e in opened if e not in mine]:
        t = words(e["title"])
        s = len(t & words(head)) / len(t) if len(t) >= 3 else 0.0
        if s > score:
            best, score = e, s
    if best and score >= 0.7:
        return best["key"], "the words of the requested title"
    if len(mine) == 1:
        return mine[0]["key"], "being the only paper requested for this entry (not verified)"
    if found:
        return found[0], "the first DOI printed in the PDF (nobody requested it)"
    return "file:" + pathlib.Path(name).stem, "nothing: unidentified"


def file_one(path, hint=None, source="inbox", name=None, key=None, how=None):
    """Move one PDF into the library. Returns its index entry, or None if it is not a PDF."""
    path = pathlib.Path(path)
    name = name or path.name
    with open(path, "rb") as f:
        if b"%PDF" not in f.read(1024):
            return None
    with index() as ix:
        if key is None:
            key, how = identify(path, name, ix, hint)
        e = ix.setdefault(key, blank(key))
        dest = PDF / (slug(key) + ".pdf")
        shutil.move(str(path), dest)
        text = pdftotext(dest)
        (TEXT / (slug(key) + ".txt")).write_text(text)
        e.update(file=rel(dest), text=rel(TEXT / (slug(key) + ".txt")), text_chars=len(text.strip()),
                 received=now(), source=source, original_name=name, identified_by=how)
        return json.loads(json.dumps(e))


def file_inbox(settle=60):
    """File every PDF in the inbox that has not been written to for `settle` seconds.
    Returns (the index entries, the names of the files that are not PDFs)."""
    INBOX.mkdir(parents=True, exist_ok=True)
    out, bad = [], []
    for p in sorted(INBOX.iterdir()):
        if p.is_file() and not p.name.startswith(".") and time.time() - p.stat().st_mtime >= settle:
            e = file_one(p)
            if e:
                out.append(e)
            else:                       # a saved login page, as a rule
                (DIR / "rejected").mkdir(exist_ok=True)
                shutil.move(str(p), DIR / "rejected" / p.name)
                bad.append(p.name)
    return out, bad


def assign(old, ref):
    """A filed PDF is in fact the paper `ref`: move it there."""
    new = key_of(ref)
    with index() as ix:
        src = next((e for e in ix.values() if e.get("file") and
                    old in (e["key"], e["file"], pathlib.Path(e["file"]).name, e.get("original_name"))), None)
        if src is None:
            return None
        e = ix.setdefault(new, blank(new))
        if e is not src:
            for ext, d, field in ((".pdf", PDF, "file"), (".txt", TEXT, "text")):
                shutil.move(str(ROOT / src[field]), d / (slug(new) + ext))
                e[field] = rel(d / (slug(new) + ext))
            for field in ("text_chars", "received", "source", "original_name"):
                e[field] = src.get(field)
            e["identified_by"] = "assigned by hand"
            if src["wanted_by"] or src["used_by"]:      # a request stays open
                src.update(file=None, text=None)
            else:
                del ix[src["key"]]
        return json.loads(json.dumps(e))


def describe(e):
    """One line for a prompt or a listing."""
    s = e["key"] + (f" — {e['title']}" if e["title"] else "")
    if e.get("file"):
        s += f"\n    {e['file']}"
        s += (f" (text: {e['text']}, {e['text_chars']} characters)" if e.get("text_chars", 0) > 500
              else " (no text layer: read the pages of the PDF itself)")
        s += f"\n    from {e['source']}" if str(e.get("source", "")).startswith(("http", "Europe")) else ""
        s += f"\n    recognised by {e.get('identified_by', '?')}"
    if e["why"]:
        s += f"\n    wanted for: {e['why']}"
    if e["wanted_by"]:
        s += f"\n    requested by: {', '.join(e['wanted_by'])}"
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("have").add_argument("ref")
    sub.add_parser("find").add_argument("ref")
    p = sub.add_parser("want")
    p.add_argument("ref")
    p.add_argument("--entry", required=True)
    p.add_argument("--title", default="")
    p.add_argument("--why", default="")
    p.add_argument("--max", type=int, default=MAX_WANTED, help="open requests allowed for this id (a piece that is not an entry may need more)")
    sub.add_parser("file")
    p = sub.add_parser("list")
    p.add_argument("--entry")
    p.add_argument("--wanted", action="store_true")
    p = sub.add_parser("assign")
    p.add_argument("old")
    p.add_argument("ref")
    o = ap.parse_args()
    if o.cmd == "have":
        e = have(o.ref)
        print(describe(e) if e else f"{key_of(o.ref)}: not in the library")
        sys.exit(0 if e else 1)
    elif o.cmd == "find":
        e = have(o.ref) or find(o.ref, verbose=True)
        print(describe(e) if e else f"{key_of(o.ref)}: no open copy could be downloaded")
        sys.exit(0 if e else 1)
    elif o.cmd == "want":
        e, msg = want(o.ref, o.entry, o.title, o.why, limit=o.max)
        print(f"{key_of(o.ref)}: {msg}")
        sys.exit(0 if e else 1)
    elif o.cmd == "file":
        filed, bad = file_inbox(settle=0)
        for e in filed:
            print(describe(e))
        for name in bad:
            print(f"{name}: not a PDF, moved to {rel(DIR / 'rejected')}/")
    elif o.cmd == "list":
        es = wanted(o.entry) if o.wanted else [
            e for e in entries().values()
            if o.entry is None or o.entry in e["wanted_by"] + e["used_by"]]
        for e in es:
            print(("have    " if e.get("file") else "wanted  ") + describe(e))
    elif o.cmd == "assign":
        e = assign(o.old, o.ref)
        print(describe(e) if e else f"{o.old}: no such paper in the library")
        sys.exit(0 if e else 1)


if __name__ == "__main__":
    main()
