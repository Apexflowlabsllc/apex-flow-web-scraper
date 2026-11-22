"""Turn two U.S. government spreadsheets into the compact data files the pay and rent packages read.

  python scripts/build-package-data.py FY26_FMRs.xlsx MSA_M2024_dl.xlsx

Sources (both U.S. government works, public domain):
  HUD Fair Market Rents FY2026   https://www.huduser.gov/portal/datasets/fmr.html
  BLS OEWS May 2024, metro areas https://www.bls.gov/oes/tables.htm  (oesm24ma.zip)
"""
import csv
import gzip
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from xlsx_rows import rows  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "src" / "rainbo_scrape" / "data"


def write(name, header, data):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(data)
    OUT.mkdir(exist_ok=True)
    with gzip.open(OUT / name, "wt", encoding="utf-8", newline="") as f:
        f.write(buf.getvalue())
    print(name, len(data), "rows", (OUT / name).stat().st_size // 1024, "KiB")


def rents(path):
    it = rows(path)
    head = next(it)
    ix = {k: i for i, k in enumerate(head)}
    areas = {}
    for r in it:
        if len(r) < len(head):
            r += [""] * (len(head) - len(r))
        name = r[ix["hud_area_name"]]
        a = areas.setdefault(name, {"state": r[ix["stusps"]], "metro": r[ix["metro"]], "fmr": [r[ix[f"fmr_{b}"]] for b in range(5)], "counties": [], "pop": 0})
        a["counties"].append(r[ix["countyname"]] + (" " + r[ix["county_town_name"]] if r[ix["county_town_name"]] else ""))
        try:
            a["pop"] += int(float(r[ix["pop2023"]] or 0))
        except ValueError:
            pass
    data = [[n, a["state"], a["metro"], a["pop"], *a["fmr"], "|".join(a["counties"])] for n, a in sorted(areas.items())]
    write("rents_fy2026.csv.gz", ["area", "state", "metro", "pop2023", "fmr_0", "fmr_1", "fmr_2", "fmr_3", "fmr_4", "counties"], data)


def wages(path):
    it = rows(path)
    head = next(it)
    ix = {k: i for i, k in enumerate(head)}
    keep = ["AREA", "AREA_TITLE", "OCC_CODE", "OCC_TITLE", "O_GROUP", "TOT_EMP", "A_MEAN", "A_PCT10", "A_PCT25", "A_MEDIAN", "A_PCT75", "A_PCT90", "H_MEDIAN"]
    data = []
    for r in it:
        if len(r) < len(head):
            r += [""] * (len(head) - len(r))
        if r[ix["O_GROUP"]] not in ("total", "major", "detailed"):
            continue
        # BLS footnotes: "*" and "**" mean no estimate; "#" means at or above the survey's top band
        # ($239,200 a year, $115.00 an hour in May 2024).
        out = [("115+" if k == "H_MEDIAN" else "239200+") if r[ix[k]] == "#" else ("" if r[ix[k]] in ("*", "**", "***") else r[ix[k]]) for k in keep]
        data.append(out)
    write("wages_may2024.csv.gz", [k.lower() for k in keep], data)


if __name__ == "__main__":
    rents(sys.argv[1])
    wages(sys.argv[2])
