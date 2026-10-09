#!/usr/bin/env python
"""Does the compound have a Wikipedia page?

    tools/env.sh tools/wikipedia.py "Xenotime" "Yttrium phosphate" "YPO4"
    tools/env.sh tools/wikipedia.py "Molybdenum silicide" --lang de

Give every name the compound has: the mineral name, the systematic name, the formula,
the name of the structure type if the compound is its prototype. For each name it prints
the page with exactly that title (following redirects) and the first search hits, each
with its address and its first sentences. Read them before linking: a page about the
element, the mineral group, a different stoichiometry or a disambiguation list is not a
page about the compound. Wikipedia is a pointer for the reader, not a source: no number
in an entry is quoted from it.
"""
import argparse, json, socket, time, urllib.error, urllib.parse, urllib.request

# Wikipedia publishes IPv6 addresses that this network cannot reach; without this every
# request waits out a 30 s timeout before falling back to IPv4.
_getaddrinfo = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: ([x for x in _getaddrinfo(*a, **k) if x[0] == socket.AF_INET]
                                      or _getaddrinfo(*a, **k))

ap = argparse.ArgumentParser()
ap.add_argument("names", nargs="+")
ap.add_argument("--lang", default="en")
ap.add_argument("--hits", type=int, default=3, help="search hits to show for each name")
o = ap.parse_args()
API = f"https://{o.lang}.wikipedia.org/w/api.php"


def ask(**params):
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json", "formatversion": 2})
    req = urllib.request.Request(url, headers={"User-Agent": "material-a-day/1.0 (github.com/hyllios/material_a_day)"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                time.sleep(0.5)                               # stay well under the rate limit
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 4:
                raise
            time.sleep(float(e.headers.get("Retry-After") or 5 * (attempt + 1)))


def pages(titles):
    """title, address, first sentences and whether it is a disambiguation page."""
    if not titles:
        return []
    q = ask(action="query", titles="|".join(titles), redirects=1, prop="extracts|info|pageprops",
            exintro=1, explaintext=1, exsentences=2, exlimit="max", inprop="url")["query"]
    return [dict(title=p["title"], url=p["fullurl"], extract=" ".join(p.get("extract", "").split()),
                 disambiguation="disambiguation" in p.get("pageprops", {}))
            for p in q.get("pages", []) if not p.get("missing") and not p.get("invalid")]


out = []
for name in o.names:
    hits = ask(action="query", list="search", srsearch=name, srlimit=o.hits)["query"]["search"]
    out.append(dict(name=name, exact=pages([name]), search=pages([h["title"] for h in hits])))
print(json.dumps(out, indent=1, ensure_ascii=False))
