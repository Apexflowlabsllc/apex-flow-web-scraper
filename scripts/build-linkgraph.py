"""Build the Apex Flow link index from a Common Crawl domain-level web graph release.

Input (download from data.commoncrawl.org/projects/hyperlinkgraph/<release>/domain/):
  domain-vertices.txt.gz  <id> <reversed domain> <hosts>
  domain-ranks.txt.gz     harmonic centrality position/value, PageRank position/value, reversed domain, hosts
  domain-edges.txt.gz     <from id> <to id>, one link between two domains per line

Output (a directory read by rainbo_scrape.linkgraph, memory-mapped, no database):
  meta.json        release, counts, caps
  keys.u64         sorted 64-bit name hashes of every indexed domain
  rec.bin          22 bytes per indexed domain (same order): position u32, all referring domains u32, strong referring
                   domains u32, in-list start u32 (into inl.bin), out-list start u32 (into outl.bin), out-list length u16;
                   in-list length is min(strong, in_cap); 0xFFFFFFFF = no list
  inl.bin          per domain: the strongest referring domains, as LEB128 deltas of their rank (0 = strongest site on the web)
  outl.bin         per strong linking site with few outbound links: the strong sites it links to (rank deltas), for co-citation
  names.bin/.off   reversed name of each of the strongest SOURCES domains, addressed by rank

"Strong" = among the SOURCES best-connected domains by harmonic centrality. Links from weaker domains are counted, not listed.
Run: py -3.12 scripts/build-linkgraph.py <input dir> <output dir> <release name>
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pcsv

SOURCES = int(os.environ.get("LG_SOURCES", 5_000_000))     # domains whose links are listed (and named)
IN_CAP = 1000            # referring domains kept per domain, strongest first
MIN_STRONG = 2           # domains linked from fewer strong sites are not indexed (unless they are strong sources)
OUT_MAX_DEGREE = 400     # co-citation lists only for linking sites with at most this many strong outbound links
STEP = time.time()


def log(msg):
    print(f"[{time.time() - STEP:7.0f}s] {msg}", flush=True)


def tsv(path, names, types):
    opts = pcsv.ReadOptions(column_names=names, block_size=1 << 26)
    parse = pcsv.ParseOptions(delimiter="\t", quote_char=False)
    conv = pcsv.ConvertOptions(column_types=types)
    return pcsv.open_csv(pa.input_stream(str(path), compression="gzip"), read_options=opts, parse_options=parse, convert_options=conv)


def name_hash(names):
    """blake2b-64 of the reversed domain, little-endian. Must match linkgraph.key()."""
    out = np.empty(len(names), dtype=np.uint64)
    for i, n in enumerate(names):
        out[i] = int.from_bytes(hashlib.blake2b(n.encode(), digest_size=8).digest(), "little")
    return out


def leb128(values, starts):
    """Delta-encode each list (lists begin at `starts`) and LEB128 the deltas. Returns bytes and per-list byte offsets."""
    d = values.astype(np.int64)
    d[1:] -= values[:-1].astype(np.int64)
    d[starts[starts < len(d)]] = values[starts[starts < len(d)]]
    d = d.astype(np.uint32)
    n = 1 + (d >= 1 << 7) + (d >= 1 << 14) + (d >= 1 << 21) + (d >= 1 << 28)
    pos = np.zeros(len(d) + 1, dtype=np.int64)
    np.cumsum(n, out=pos[1:])
    out = np.zeros(pos[-1], dtype=np.uint8)
    for k in range(5):
        m = n > k
        byte = ((d[m] >> np.uint32(7 * k)) & np.uint32(0x7F)).astype(np.uint8)
        byte |= np.where(n[m] > k + 1, 0x80, 0).astype(np.uint8)
        out[pos[:-1][m] + k] = byte
    return out, pos[starts]


def main(src, dst, release):
    src, dst = Path(src), Path(dst)
    dst.mkdir(parents=True, exist_ok=True)

    log("vertices")
    names = pa.concat_arrays([b.column(1) for b in tsv(src / "domain-vertices.txt.gz", ["id", "name", "hosts"],
                                                        {"id": pa.int32(), "name": pa.large_string(), "hosts": pa.int32()})])
    total = len(names)
    log(f"{total:,} domains")

    log("ranks")
    rank_pos, rank_names = [], []
    reader = tsv(src / "domain-ranks.txt.gz", ["hc_pos", "hc_val", "pr_pos", "pr_val", "name", "hosts"],
                 {"hc_pos": pa.string(), "hc_val": pa.string(), "pr_pos": pa.string(), "pr_val": pa.string(),
                  "name": pa.large_string(), "hosts": pa.string()})
    for b in reader:
        p = b.column(0)
        keep = pc.invert(pc.starts_with(p, "#"))
        rank_pos.append(pc.cast(pc.filter(p, keep), pa.int64()))
        rank_names.append(pc.filter(b.column(4), keep))
    rank_pos = pa.concat_arrays(rank_pos).to_numpy()
    rank_names = pa.concat_arrays(rank_names)
    ids = pc.index_in(rank_names, value_set=names)
    if ids.null_count:
        raise SystemExit(f"{ids.null_count} rank names missing from vertices")
    ids = ids.to_numpy()
    pos_of = np.full(total, 0xFFFFFFFF, dtype=np.uint32)         # 1-based harmonic centrality position
    pos_of[ids.astype(np.int64)] = rank_pos.astype(np.uint32)
    del rank_names, ids
    log(f"ranked {len(rank_pos):,}; unranked {(pos_of == 0xFFFFFFFF).sum():,}")
    # Source rank (0-based) for the strongest SOURCES domains; everything else is a weak source.
    srank = pos_of.astype(np.int64) - 1
    strong = srank < SOURCES
    by_rank = np.full(SOURCES, -1, dtype=np.int64)
    by_rank[srank[strong]] = np.nonzero(strong)[0]
    if (by_rank < 0).any():
        raise SystemExit("rank positions are not contiguous")

    log("edges")
    indeg = np.zeros(total, dtype=np.uint32)
    inkeys, outkeys, pending = [], [], []
    count = 0
    for b in tsv(src / "domain-edges.txt.gz", ["f", "t"], {"f": pa.int32(), "t": pa.int32()}):
        f = b.column(0).to_numpy()
        t = b.column(1).to_numpy()
        count += len(f)
        pending.append(t)
        if sum(len(x) for x in pending) > 150_000_000:
            indeg += np.bincount(np.concatenate(pending), minlength=total).astype(np.uint32)
            pending.clear()
        sf = srank[f]
        m = sf < SOURCES
        inkeys.append((t[m].astype(np.uint64) << np.uint64(23)) | sf[m].astype(np.uint64))
        st = srank[t]
        m2 = m & (st < SOURCES)
        outkeys.append((sf[m2].astype(np.uint64) << np.uint64(23)) | st[m2].astype(np.uint64))
        if count % 200_000_000 < len(f):
            log(f"{count:,} links read")
    if pending:
        indeg += np.bincount(np.concatenate(pending), minlength=total).astype(np.uint32)
        pending.clear()
    log(f"{count:,} links")

    inkeys = np.concatenate(inkeys)
    log(f"{len(inkeys):,} links from strong sources; sorting")
    inkeys.sort()
    tgt = (inkeys >> np.uint64(23)).astype(np.int64)
    src_r = (inkeys & np.uint64((1 << 23) - 1)).astype(np.uint32)
    del inkeys
    starts = np.flatnonzero(np.r_[True, tgt[1:] != tgt[:-1]])
    strong_in = np.diff(np.r_[starts, len(tgt)]).astype(np.uint32)
    targets = tgt[starts]
    # Index a domain when at least MIN_STRONG strong sites link to it, or when it is a strong source itself.
    wanted = (strong_in >= MIN_STRONG) | (srank[targets] < SOURCES)
    targets, strong_in = targets[wanted], strong_in[wanted]
    # keep the strongest IN_CAP per indexed domain (lists are sorted by source rank = strongest first)
    rank_in_list = np.arange(len(tgt)) - np.repeat(starts, np.diff(np.r_[starts, len(tgt)]))
    keep = (rank_in_list < IN_CAP) & np.repeat(wanted, np.diff(np.r_[starts, len(tgt)]))
    src_r, tgt = src_r[keep], tgt[keep]
    starts = np.flatnonzero(np.r_[True, tgt[1:] != tgt[:-1]])
    inbytes, inoff = leb128(src_r, starts)
    del src_r, tgt, rank_in_list, keep
    log(f"in-lists {len(inbytes)/1e6:,.0f} MB for {len(targets):,} domains")

    log("co-citation lists")
    outkeys = np.concatenate(outkeys)
    outkeys.sort()
    s = (outkeys >> np.uint64(23)).astype(np.int64)
    o = (outkeys & np.uint64((1 << 23) - 1)).astype(np.uint32)
    del outkeys
    st = np.flatnonzero(np.r_[True, s[1:] != s[:-1]])
    deg = np.diff(np.r_[st, len(s)])
    ok = np.repeat(deg <= OUT_MAX_DEGREE, deg)
    s, o = s[ok], o[ok]
    st = np.flatnonzero(np.r_[True, s[1:] != s[:-1]])
    outsrc = s[st]                           # source ranks that have a list
    outcnt = np.diff(np.r_[st, len(s)]).astype(np.uint32)
    outbytes, outoff = leb128(o, st)
    del s, o, ok
    log(f"out-lists {len(outbytes)/1e6:,.0f} MB for {len(outsrc):,} sites")

    # Indexed domains: every domain with a strong referring domain, plus every strong source.
    idx = np.union1d(targets, by_rank)
    log(f"{len(idx):,} indexed domains; hashing")
    keys = name_hash(names.take(pa.array(idx)).to_pylist())
    order = np.argsort(keys)
    keys, idx = keys[order], idx[order]
    if (np.diff(keys) == 0).any():
        raise SystemExit("hash collision")

    rec = np.zeros(len(idx), dtype=np.dtype([("pos", "<u4"), ("refs", "<u4"), ("strong", "<u4"), ("inl", "<u4"), ("outl", "<u4"), ("outn", "<u2")], align=False))
    rec["pos"] = pos_of[idx]
    rec["refs"] = indeg[idx]
    NONE = np.uint32(0xFFFFFFFF)
    if len(inbytes) >= NONE or len(outbytes) >= NONE:
        raise SystemExit("list blob too large for 32-bit offsets")
    rec["inl"] = NONE
    rec["outl"] = NONE
    j = np.searchsorted(targets, idx)
    hit = (j < len(targets)) & (targets[np.minimum(j, len(targets) - 1)] == idx)
    rec["strong"][hit] = strong_in[j[hit]]
    rec["inl"][hit] = inoff[j[hit]]
    sr = srank[idx]
    j2 = np.searchsorted(outsrc, sr)
    hit2 = (sr < SOURCES) & (j2 < len(outsrc)) & (outsrc[np.minimum(j2, len(outsrc) - 1)] == sr)
    rec["outl"][hit2] = outoff[j2[hit2]]
    rec["outn"][hit2] = outcnt[j2[hit2]]

    keys.astype("<u8").tofile(dst / "keys.u64")
    rec.tofile(dst / "rec.bin")
    inbytes.tofile(dst / "inl.bin")
    outbytes.tofile(dst / "outl.bin")

    log("source names")
    snames = names.take(pa.array(by_rank)).to_pylist()
    blob = "\n".join(snames).encode()
    off = np.zeros(SOURCES + 1, dtype="<u4")
    np.cumsum([len(n.encode()) + 1 for n in snames], out=off[1:])
    (dst / "names.bin").write_bytes(blob)
    off.tofile(dst / "names.off")

    meta = {"release": release, "source": "Common Crawl web graph (domain level), commoncrawl.org/web-graphs",
            "domains_in_graph": int(total), "links_in_graph": int(count), "indexed": int(len(idx)),
            "strong_sources": SOURCES, "in_cap": IN_CAP, "min_strong": MIN_STRONG, "out_max_degree": OUT_MAX_DEGREE, "built": time.strftime("%Y-%m-%d")}
    (dst / "meta.json").write_text(json.dumps(meta, indent=1))
    log("done " + json.dumps(meta))


if __name__ == "__main__":
    main(*sys.argv[1:4])
