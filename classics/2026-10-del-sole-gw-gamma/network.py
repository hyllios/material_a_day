#!/usr/bin/env python3
"""How close the GW literature sits to Del Sole and Bechstedt, in co-authorship.

    tools/env.sh classics/2026-10-del-sole-gw-gamma/network.py pull       # OpenAlex -> data/survey/network/
    tools/env.sh classics/2026-10-del-sole-gw-gamma/network.py analyse    # -> network.json
    tools/env.sh classics/2026-10-del-sole-gw-gamma/network.py figures    # -> fig1_{light,dark}.png

The GW papers are the works whose title or abstract contains one of the phrases in GW,
with at most 30 authors (larger collaborations say little about who taught whom). For
each paper we take the smallest number of co-authorship steps between any of its authors
and a group of people (zero: one of them wrote it; one: someone who published with them).
The network is made of these papers and of every paper of the people compared. A paper
is European when every affiliation it lists is in a European country, "elsewhere" when
none is; papers with both, or with none recorded, are left out.

Co-authorship is not supervision, OpenAlex merges and splits people imperfectly, and a
phrase search misses papers that do not say "GW" in the abstract. The numbers describe
where the literature sits, not who trained whom.
"""
import collections, json, pathlib, socket, sys, urllib.parse, urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CACHE = ROOT / "data" / "survey" / "network"
GW = ['"GW calculations"', '"GW approximation"', '"GW method"', '"G0W0"', '"GW self-energy"', '"GW quasiparticle"']
PEOPLE = {    # OpenAlex author records; Del Sole is split over three
    "Del Sole": ["A5090181808", "A5110272250", "A5155458726"],
    "Bechstedt": ["A5021512784"],
    "Louie": ["A5045377460"],
    "Hybertsen": ["A5069552382"],
}
GROUPS = {"Del Sole or Bechstedt": ["Del Sole", "Bechstedt"], "Del Sole": ["Del Sole"],
          "Bechstedt": ["Bechstedt"], "Louie or Hybertsen": ["Louie", "Hybertsen"]}
EUROPE = set("AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE GB CH NO IS".split())
BINS = [(1985, 1994), (1995, 1999), (2000, 2004), (2005, 2009), (2010, 2014), (2015, 2019), (2020, 2026)]
MAX_AUTHORS = 30

_g = socket.getaddrinfo                     # the IPv6 route of this machine times out
socket.getaddrinfo = lambda h, p, f=0, *a, **k: _g(h, p, socket.AF_INET, *a, **k)


def get(path, **q):
    u = "https://api.openalex.org/" + path + "?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(u, headers={"User-Agent": "material-a-day (https://github.com/hyllios/material_a_day)"})
    return json.loads(urllib.request.urlopen(req, timeout=120).read())


def works(flt):
    out, cur = [], "*"
    while cur:
        r = get("works", filter=flt, per_page=200, cursor=cur,
                select="id,doi,title,publication_year,cited_by_count,type,authorships")
        out += r["results"]
        cur = r["meta"].get("next_cursor") if r["results"] else None
    return out


def pull():
    CACHE.mkdir(parents=True, exist_ok=True)
    gw = works("title_and_abstract.search:" + "|".join(GW))
    (CACHE / "gw.json").write_text(json.dumps(gw))
    people = {k: [w for a in v for w in works(f"author.id:{a}")] for k, v in PEOPLE.items()}
    (CACHE / "people.json").write_text(json.dumps(people))
    print(f"{len(gw)} GW works; " + ", ".join(f"{k} {len(v)}" for k, v in people.items()))


def ids(w):
    return {x["author"]["id"].split("/")[-1] for x in w["authorships"] if (x.get("author") or {}).get("id")}


def region(w):
    cc = {c for x in w["authorships"] for c in (x.get("countries") or [])}
    return "Europe" if cc and cc <= EUROPE else "elsewhere" if cc and not cc & EUROPE else None


def analyse():
    gw = json.loads((CACHE / "gw.json").read_text())
    people = json.loads((CACHE / "people.json").read_text())
    every = {w["id"]: w for w in gw + [w for v in people.values() for w in v]}
    adj = collections.defaultdict(set)
    for w in every.values():
        if len(w["authorships"]) <= MAX_AUTHORS:
            a = ids(w)
            for i in a:
                adj[i] |= a
    dist = {}
    for g, members in GROUPS.items():
        d = {a: 0 for m in members for a in PEOPLE[m]}
        front = list(d)
        while front:
            nxt = []
            for u in front:
                for v in adj[u]:
                    if v not in d:
                        d[v] = d[u] + 1
                        nxt.append(v)
            front = nxt
        dist[g] = d
    papers = [w for w in gw if w.get("type") in ("article", "preprint", "review")
              and len(w["authorships"]) <= MAX_AUTHORS and w.get("publication_year")]
    step = lambda w, g: min((dist[g].get(a, 99) for a in ids(w)), default=99)
    N = {"papers": len(papers), "regions": collections.Counter(str(region(w)) for w in papers),
         "coauthors": {k: len({a for w in people[k] if len(w["authorships"]) <= MAX_AUTHORS for a in ids(w)} - set(PEOPLE[k]))
                       for k in PEOPLE}, "bins": {}}
    for lo, hi in BINS:
        for reg in ("Europe", "elsewhere"):
            ws = [w for w in papers if lo <= w["publication_year"] <= hi and region(w) == reg]
            N["bins"][f"{lo}-{hi} {reg}"] = {"papers": len(ws), **{
                g: {f"within {n}": round(sum(step(w, g) <= n for w in ws) / max(len(ws), 1), 3) for n in (0, 1, 2)}
                for g in GROUPS}}
    eu = [w for w in papers if 2005 <= w["publication_year"] <= 2014 and region(w) == "Europe"]
    co = {a for g in ("Del Sole", "Bechstedt") for a, s in dist[g].items() if s == 1}
    names = collections.Counter(x["author"]["display_name"] for w in eu for x in w["authorships"]
                                if ((x.get("author") or {}).get("id") or "").split("/")[-1] in co)
    N["bridges_europe_2005_2014"] = names.most_common(25)
    (HERE / "network.json").write_text(json.dumps(N, indent=1, ensure_ascii=False))
    for k, v in N["bins"].items():
        print(k.ljust(20), v["papers"], {g: v[g]["within 1"] for g in GROUPS})


def figures():
    import subprocess, tempfile
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    N = json.loads((HERE / "network.json").read_text())
    ink, muted, blue, orange = "#0b0b0b", "#898781", "#2a78d6", "#eb6834"
    plt.rcParams.update({"font.size": 10, "axes.edgecolor": muted, "axes.labelcolor": ink,
                         "xtick.color": muted, "ytick.color": muted, "text.color": ink})
    fig, axs = plt.subplots(1, 2, figsize=(9, 3.6), sharey=True)
    x = [(lo + hi) / 2 for lo, hi in BINS]
    for ax, reg in zip(axs, ("Europe", "elsewhere")):
        for g, c in (("Del Sole or Bechstedt", blue), ("Louie or Hybertsen", orange)):
            y = [100 * N["bins"][f"{lo}-{hi} {reg}"][g]["within 1"] for lo, hi in BINS]
            ax.plot(x, y, color=c, lw=2, marker="o", ms=5, label=g + ", or a co-author")
        n = [N["bins"][f"{lo}-{hi} {reg}"]["papers"] for lo, hi in BINS]
        ax.set_title("all authors in Europe" if reg == "Europe" else "no author in Europe", fontsize=10, loc="left")
        ax.set_xticks(x, [f"{lo}–{str(hi)[2:]}\n({ni})" for (lo, hi), ni in zip(BINS, n)], fontsize=7.5)
        ax.set_ylim(0, 70)
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", color="#e4e2dc", lw=0.6)
    axs[0].set_ylabel("GW papers with such an author (%)")
    axs[0].legend(frameon=False, fontsize=8.5, loc="upper left")
    fig.text(0.5, -0.02, "in brackets: GW papers in each period", ha="center", fontsize=8, color=muted)
    fig.tight_layout()
    with tempfile.TemporaryDirectory() as t:
        fig.savefig(f"{t}/f.png", dpi=200, bbox_inches="tight", facecolor="white")
        subprocess.run([sys.executable, str(ROOT / "tools" / "plot.py"), f"{t}/f.png", str(HERE / "fig1")], check=True)


if __name__ == "__main__":
    {"pull": pull, "analyse": analyse, "figures": figures}[sys.argv[1]]()
