"""Merge every source in sources.txt into one AltStore / SideStore / Feather / ESign repo.json.

Usage: python build.py   (writes repo.json next to this file)
Flat list like Alan's repo: one entry per app per source, duplicate bundle IDs allowed,
identical download URLs kept only once (first source in sources.txt wins).
"""
import concurrent.futures as cf
import datetime
import gzip
import json
import pathlib
import re
import sys
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).parent
SOURCE_URL = "https://raw.githubusercontent.com/ad3333m/adams-gigantic-repo/main/repo.json"
TODAY = datetime.date.today().isoformat()


def fetch(url):
    req = urllib.request.Request(urllib.parse.quote(url, safe=":/?=&%+~"),
                                 headers={"User-Agent": "AltStore/2.0", "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            b = r.read()
        if b[:2] == b"\x1f\x8b":
            b = gzip.decompress(b)
        text = b.decode("utf-8-sig")
        try:
            d = json.loads(text)
        except json.JSONDecodeError:  # lots of hand-written sources leave trailing commas
            d = json.loads(re.sub(r",\s*([\]}])", r"\1", text))
        return d if isinstance(d, dict) and isinstance(d.get("apps"), list) else "not an AltStore/ESign source"
    except Exception as e:
        return f"{type(e).__name__}: {e}"[:120]


def as_int(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return 0


def versions(a):
    vs = a.get("versions") if isinstance(a.get("versions"), list) else []
    if not vs:  # legacy / ESign entry: single build at the top level
        vs = [{"version": a.get("version"), "date": a.get("versionDate"), "size": a.get("size"),
               "downloadURL": a.get("downloadURL") or a.get("down"),
               "localizedDescription": a.get("versionDescription")}]
    # AltStore refuses the whole source if one version lacks version/date/size, so fill them in
    return [{**v, "version": str(v.get("version") or a.get("version") or "1.0"),
             "date": str(v.get("date") or a.get("versionDate") or TODAY),
             "size": as_int(v.get("size")), "downloadURL": v["downloadURL"].strip()}
            for v in vs if isinstance(v, dict) and str(v.get("downloadURL") or "").strip().startswith("http")]


def merge(sources):
    apps, seen, report = [], set(), []
    for url, d in sources:
        if isinstance(d, str):
            report.append(f"FAIL {url}  ({d})")
            continue
        name, before = str(d.get("name") or url), len(apps)
        for a in d["apps"]:
            if not isinstance(a, dict) or not a.get("name") or not (a.get("bundleIdentifier") or a.get("bundleID")):
                continue
            vs = [v for v in versions(a) if v["downloadURL"] not in seen]
            if not vs:
                continue
            seen.update(v["downloadURL"] for v in vs)
            v, icon = vs[0], str(a.get("iconURL") or a.get("icon") or "")
            bundle = str(a.get("bundleIdentifier") or a.get("bundleID"))
            desc = str(a.get("localizedDescription") or v.get("localizedDescription") or a.get("subtitle") or "")
            apps.append({**a, "name": str(a["name"]), "bundleIdentifier": bundle, "bundleID": bundle,
                         "developerName": str(a.get("developerName") or name), "iconURL": icon, "icon": icon,
                         "localizedDescription": f"{desc}\n\nVia {name}".strip(),
                         "version": v["version"], "versionDate": v["date"], "size": v["size"],
                         "downloadURL": v["downloadURL"], "versions": vs})
        report.append(f"{len(apps) - before:6}  {name}  <{url}>")
    return apps, report


def check(apps):
    for a in apps:
        assert a["name"] and a["bundleIdentifier"] and a["downloadURL"].startswith("http"), a.get("name")
        assert all(isinstance(v["size"], int) and v["date"] and v["version"] for v in a["versions"]), a["name"]


if __name__ == "__main__":
    urls = list(dict.fromkeys(l.strip() for l in (HERE / "sources.txt").read_text(encoding="utf-8").splitlines()
                              if l.strip().startswith("http")))
    with cf.ThreadPoolExecutor(32) as pool:
        fetched = list(zip(urls, pool.map(fetch, urls)))
    apps, report = merge(fetched)
    check(apps)
    used = sum(1 for _, d in fetched if not isinstance(d, str))
    repo = {"name": "Adam's Gigantic Repo", "identifier": "com.ad3333m.gigantic",
            "subtitle": f"{len(apps):,} apps merged from {used} sources, Alan's Gigantic Repo included",
            "sourceURL": SOURCE_URL, "website": "https://github.com/ad3333m/adams-gigantic-repo",
            "tintColor": "7B5CFF", "featuredApps": [], "news": [], "apps": apps}
    (HERE / "repo.json").write_text(json.dumps(repo, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (HERE / "report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print("\n".join(report))
    print(f"\n{len(apps):,} apps from {used}/{len(urls)} sources -> repo.json")
