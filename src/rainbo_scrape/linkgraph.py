"""Read the Apex Flow link index: which domains link to which, and how strong each domain is.

The index is built by scripts/build-linkgraph.py from Common Crawl's domain-level web graph and memory-mapped
here, so a lookup is a binary search and a short list decode: no database, no outside call.

Authority is 0-1000 from the domain's harmonic-centrality position among every domain in the graph
(1000 = the best-connected site on the web). "Strong" referring domains are the best-connected few million
domains; links from weaker domains are counted but not listed.
"""
import hashlib
import json
import math
import mmap
import os
import re
import struct
from pathlib import Path

REC = struct.Struct("<IIIIIH")
NONE = 0xFFFFFFFF
UNRANKED = 0xFFFFFFFF
DEFAULT_DIR = "/opt/apex-webscraper/linkgraph"
LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
# One referring row here is many unrelated sites: shared hosting, site builders, shorteners, CDNs.
PLATFORMS = {
    "blogspot.com", "wordpress.com", "wordpress.org", "wix.com", "wixsite.com", "squarespace.com", "weebly.com",
    "tumblr.com", "medium.com", "substack.com", "github.io", "github.com", "gitlab.io", "netlify.app", "vercel.app",
    "pages.dev", "herokuapp.com", "firebaseapp.com", "web.app", "appspot.com", "azurewebsites.net", "cloudfront.net",
    "amazonaws.com", "googleusercontent.com", "googleapis.com", "gstatic.com", "cloudflare.com", "akamaihd.net",
    "bit.ly", "t.co", "tinyurl.com", "ow.ly", "goo.gl", "linktr.ee", "lnkd.in", "buff.ly", "jsdelivr.net",
    "unpkg.com", "cdnjs.com", "fastly.net", "shopify.com", "myshopify.com", "godaddysites.com", "jimdo.com",
    "strikingly.com", "carrd.co", "webflow.io", "ning.com", "livejournal.com", "typepad.com", "over-blog.com",
    "dev.to", "hashnode.dev", "beehiiv.com", "ghost.io", "notion.site", "gitbook.io", "readthedocs.io", "framer.website",
    "framer.app", "wpengine.com", "hubspot.com", "hs-sites.com", "googletagmanager.com", "google-analytics.com",
    "doubleclick.net", "schema.org", "w3.org", "gravatar.com", "wp.com", "fonts.com", "typekit.net", "bootstrapcdn.com",
}


def key(reversed_name):
    """64-bit name hash. Must match name_hash() in scripts/build-linkgraph.py."""
    return int.from_bytes(hashlib.blake2b(reversed_name.encode(), digest_size=8).digest(), "little")


def flip(name):
    return ".".join(reversed(name.split(".")))


def clean(text):
    """example.com from 'https://www.Example.com/path', or ValueError."""
    if not isinstance(text, str) or not 3 <= len(text.strip()) <= 300:
        raise ValueError("domain must be a site such as \"example.com\"")
    host = text.strip().lower()
    host = re.sub(r"^[a-z][a-z0-9+.-]*://", "", host)
    host = re.split(r"[/?#]", host, maxsplit=1)[0]
    host = host.rsplit("@", 1)[-1].split(":")[0].strip(".")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise ValueError("domain must be a site such as \"example.com\"")
    labels = host.split(".")
    if len(labels) < 2 or not all(LABEL.match(x) for x in labels) or labels[-1].isdigit():
        raise ValueError("domain must be a site such as \"example.com\" (not an IP address)")
    return host


def _leb(buf, start, count):
    """Decode `count` LEB128 deltas starting at byte `start` into absolute values."""
    out, value, total, shift, pos = [], 0, 0, 0, start
    while len(out) < count:
        b = buf[pos]
        pos += 1
        value |= (b & 0x7F) << shift
        if b & 0x80:
            shift += 7
            continue
        total += value
        out.append(total)
        value, shift = 0, 0
    return out


class LinkGraph:
    def __init__(self, path):
        path = Path(path)
        self.path = path
        self.meta = json.loads((path / "meta.json").read_text())
        self._files = []
        self.keys = self._map(path / "keys.u64").cast("Q")
        self.rec = self._map(path / "rec.bin")
        self.inl = self._map(path / "inl.bin")
        self.outl = self._map(path / "outl.bin")
        self.names = self._map(path / "names.bin")
        self.off = self._map(path / "names.off").cast("I")
        self.count = len(self.keys)
        self.total = self.meta["domains_in_graph"]
        self.cap = self.meta["in_cap"]

    def _map(self, file):
        f = open(file, "rb")
        self._files.append(f)
        if os.path.getsize(file) == 0:
            return memoryview(b"")
        return memoryview(mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ))

    # -- lookup ---------------------------------------------------------------------------------
    def _index(self, name):
        k, lo, hi = key(flip(name)), 0, self.count
        while lo < hi:
            mid = (lo + hi) // 2
            if self.keys[mid] < k:
                lo = mid + 1
            else:
                hi = mid
        return lo if lo < self.count and self.keys[lo] == k else None

    def find(self, domain):
        """(registered domain, row) for a host, trying blog.shop.example.co.uk, shop.example.co.uk, ... in turn."""
        host = clean(domain)
        labels = host.split(".")
        for i in range(len(labels) - 1):
            name = ".".join(labels[i:])
            row = self._index(name)
            if row is not None:
                return name, row
        return host, None

    def name(self, rank):
        return flip(bytes(self.names[self.off[rank]:self.off[rank + 1] - 1]).decode())

    def authority(self, position):
        """0-1000 from a 1-based harmonic-centrality position."""
        if not position or position >= UNRANKED:
            return 0
        return max(0, min(1000, round(1000 * (1 - math.log(position) / math.log(self.total)))))

    def rank_authority(self, rank):
        return self.authority(rank + 1)

    def record(self, row):
        pos, refs, strong, inl, outl, outn = REC.unpack_from(self.rec, row * REC.size)
        return {"position": pos, "referring_domains": refs, "strong_referring_domains": strong, "outn": outn, "inl": inl, "outl": outl}

    def referrers(self, row):
        """Ranks of the strongest referring domains, strongest first (at most in_cap)."""
        r = self.record(row)
        if r["inl"] == NONE:
            return []
        return _leb(self.inl, r["inl"], min(r["strong_referring_domains"], self.cap))

    def outlinks(self, row):
        r = self.record(row)
        if r["outl"] == NONE:
            return []
        return _leb(self.outl, r["outl"], r["outn"])

    # -- answers --------------------------------------------------------------------------------
    def summary(self, domain):
        name, row = self.find(domain)
        base = {"domain": name, "asked": domain, "graph_release": self.meta["release"]}
        if row is None:
            return {**base, "found": False, "authority": 0, "referring_domains": 0, "strong_referring_domains": 0,
                    "note": "Not in this release of the link index: fewer than two of the web's five million best-connected sites linked to it when the web was last crawled."}
        r = self.record(row)
        return {**base, "found": True, "authority": self.authority(r["position"]),
                "global_rank": r["position"] if r["position"] < UNRANKED else None,
                "referring_domains": r["referring_domains"], "strong_referring_domains": r["strong_referring_domains"],
                "domains_ranked": self.total}

    def referring(self, domain, limit=100, offset=0, min_authority=0, skip_platforms=False):
        name, row = self.find(domain)
        ranks = self.referrers(row) if row is not None else []
        rows = []
        for rank in ranks:
            a = self.rank_authority(rank)
            if a < min_authority:
                break                      # lists are strongest first
            n = self.name(rank)
            if skip_platforms and _platform(n):
                continue
            rows.append({"domain": n, "authority": a, "global_rank": rank + 1})
        total = len(rows)
        return {"domain": name, "found": row is not None, "returned": len(rows[offset:offset + limit]), "listed": total,
                "strong_referring_domains": self.record(row)["strong_referring_domains"] if row is not None else 0,
                "referring_domains": rows[offset:offset + limit], "graph_release": self.meta["release"],
                "note": f"Strongest first; at most {self.cap} listed per domain. Links from weaker domains are counted in authority answers, not listed."}

    def gap(self, domain, competitors, min_competitors=2, min_authority=100, exclude=(), include_earned=True, limit=50):
        me, my_row = self.find(domain)
        mine = set(self.referrers(my_row)) if my_row is not None else set()
        drop = {clean(x) for x in exclude}
        seen, coverage = {}, []
        names = []
        for c in competitors:
            cname, crow = self.find(c)
            if cname == me or cname in names:
                continue
            names.append(cname)
            refs = self.referrers(crow) if crow is not None else []
            coverage.append({"competitor": cname, "found": crow is not None, "strong_referring_domains": len(refs),
                             "authority": self.authority(self.record(crow)["position"]) if crow is not None else 0})
            for rank in refs:
                seen.setdefault(rank, []).append(cname)
        rows, funnel = [], {"union": len(seen), "after_filters": 0, "gap": 0, "earned": 0}
        for rank, who in seen.items():
            if len(who) < min_competitors:
                continue
            a = self.rank_authority(rank)
            if a < min_authority:
                continue
            n = self.name(rank)
            if n == me or n in names or _platform(n) or any(n == d or n.endswith("." + d) for d in drop):
                continue
            earned = rank in mine
            funnel["after_filters"] += 1
            funnel["earned" if earned else "gap"] += 1
            if earned and not include_earned:
                continue
            rows.append({"domain": n, "authority": a, "competitor_count": len(who), "links_to": who, "earned": earned})
        rows.sort(key=lambda x: (-x["competitor_count"], -x["authority"]))
        return {"domain": me, "competitors": coverage, "summary": funnel, "returned": min(limit, len(rows)),
                "gap": rows[:limit], "graph_release": self.meta["release"],
                "note": "Sites that link to several of your competitors, strongest first. earned=true means they already link to you."}

    def competitors(self, domain, limit=20):
        """Co-citation: sites that the same niche linkers link to. Big general linkers are left out; each shared linker
        counts less the more sites it links to."""
        name, row = self.find(domain)
        if row is None:
            return {"domain": name, "found": False, "competitors": [], "note": "Not in this release of the link graph."}
        me = self._index(name)
        scores, shared = {}, {}
        own_rank = self.record(row)["position"] - 1
        for rank in self.referrers(row)[:600]:
            linker = self._index(self.name(rank))
            if linker is None:
                continue
            outs = self.outlinks(linker)
            if not outs or len(outs) < 2:
                continue
            w = 1 / math.log(2 + len(outs))
            for o in outs:
                if o == own_rank:
                    continue
                scores[o] = scores.get(o, 0) + w
                shared[o] = shared.get(o, 0) + 1
        # Cosine-style similarity: shared niche linkers divided by the square root of how many strong sites link to the
        # candidate, so giants that everyone links to (search engines, social networks) do not crowd out real rivals.
        rows = []
        for o in sorted((o for o in scores if shared[o] >= 3), key=lambda o: -scores[o])[:3000]:
            n = self.name(o)
            if _platform(n):
                continue
            crow = self._index(n)
            popularity = self.record(crow)["strong_referring_domains"] if crow is not None else 1
            rows.append({"domain": n, "authority": self.rank_authority(o), "shared_referring_domains": shared[o],
                         "score": round(scores[o] / math.sqrt(max(popularity, 1)), 4)})
        rows.sort(key=lambda r: -r["score"])
        rows = rows[:limit]
        return {"domain": name, "found": me is not None, "competitors": rows, "graph_release": self.meta["release"],
                "note": "Found from shared links: sites that the same niche linkers also link to. Large directories and hosting platforms are left out."}


def _platform(name):
    return any(name == p or name.endswith("." + p) for p in PLATFORMS)


_graph = None


def graph():
    """The server's link index, opened once. Raises FileNotFoundError when it is not installed."""
    global _graph
    if _graph is None:
        _graph = LinkGraph(os.environ.get("APEX_LINKGRAPH_DIR", DEFAULT_DIR))
    return _graph
