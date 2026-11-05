"""Scrape one URL in a fresh process and print one RESULT JSON line of metrics."""
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from rainbo_scrape.settings import Settings
from rainbo_scrape.engine import scrape, validate_request, Fetcher


def peak_mb():
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        c = Counters()
        c.cb = ctypes.sizeof(Counters)
        kernel = ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        ctypes.windll.psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(c), c.cb)
        return round(c.PeakWorkingSetSize / 1048576, 1)
    import resource
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


async def main(url, render, outfile):
    settings = Settings(data_dir=Path(os.environ.get("RAINBO_SCRAPE_DATA", ".bench-data")))
    options = validate_request({"urls": [url], "max_pages": 1, "max_depth": 0, "render": render, "cache": False}, settings)
    row = {"url": url, "ok": False}
    started = time.monotonic()
    try:
        async with Fetcher(settings, None) as fetcher:
            async with asyncio.timeout(90):
                r = await scrape(fetcher, options["urls"][0], options)
        Path(outfile).write_text(r.get("markdown") or r.get("text") or "", encoding="utf-8")
        row.update(ok=True, status=r["status"], rendered=r["rendered"], kind=r["kind"],
                   text_chars=len(r.get("text", "")), md_chars=len(r.get("markdown", "")),
                   headings=len(r.get("headings", [])), tables=len(r.get("tables", [])),
                   links=len(r.get("links", [])), title=(r.get("title") or "")[:100], bytes=r["bytes"])
    except Exception as exc:
        row.update(error=getattr(exc, "code", type(exc).__name__), message=str(exc)[:160])
    row.update(elapsed_ms=round((time.monotonic() - started) * 1000), peak_mb=peak_mb())
    print("RESULT " + json.dumps(row))


asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3]))
