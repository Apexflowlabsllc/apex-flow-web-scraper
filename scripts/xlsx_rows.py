"""Minimal .xlsx reader (standard library only): yields each row of the first sheet as a list of strings.
Used by build-package-data.py to turn government spreadsheets into compact data files."""
import re
import zipfile
from xml.etree import ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _col(ref):
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def rows(path, sheet=1):
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.parse(z.open("xl/sharedStrings.xml")).getroot().findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.iter("{%s}t" % NS["m"])))
        with z.open(f"xl/worksheets/sheet{sheet}.xml") as f:
            for _, el in ET.iterparse(f):
                if el.tag != "{%s}row" % NS["m"]:
                    continue
                out = []
                for c in el.findall("m:c", NS):
                    i = _col(c.get("r"))
                    while len(out) < i:
                        out.append("")
                    v = c.find("m:v", NS)
                    if c.get("t") == "s" and v is not None:
                        out.append(shared[int(v.text)])
                    elif c.get("t") == "inlineStr":
                        out.append("".join(t.text or "" for t in c.iter("{%s}t" % NS["m"])))
                    else:
                        out.append(v.text if v is not None else "")
                yield out
                el.clear()


if __name__ == "__main__":
    import sys
    for k, r in enumerate(rows(sys.argv[1])):
        print(r)
        if k > int(sys.argv[2] if len(sys.argv) > 2 else 3):
            break
