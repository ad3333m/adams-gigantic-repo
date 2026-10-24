"""Merge every source in sources.txt into one AltStore / SideStore / Feather / KravaSign / ESign repo.json.

Usage: python build.py   (writes repo.json next to this file)
Flat list like Alan's repo: one entry per app per source, duplicate bundle IDs allowed,
identical download URLs kept only once (first source in sources.txt wins).
Every field is re-typed on the way out: Feather-based apps (KravaSign) reject the whole
repo if a single app has a bad URL, unreadable date, missing icon or AltStore PAL listing.
"""
import concurrent.futures as cf
import datetime
import email.utils
import gzip
import json
import pathlib
import re
import sys
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).parent
SOURCE_URL = "https://raw.githubusercontent.com/ad3333m/adams-gigantic-repo/main/repo.json"
TODAY = datetime.date.today().isoformat() + "T00:00:00Z"
URL_OK = re.compile(r"https?://[\w\-.~:/?#\[\]@!$&'()*+,;=%]+")
DATE_OK = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")


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
    except (TypeError, ValueError, OverflowError):
        return 0


def url(u):
    """Percent-encode so Swift's URL(string:) accepts it on every iOS version; '' if it isn't a web URL."""
    u = re.sub(r"%(?![0-9A-Fa-f]{2})", "%25", str(u or "").strip())
    u = urllib.parse.quote(u, safe=":/?#[]@!$&'()*+,;=%~")
    return u if URL_OK.fullmatch(u) else ""


def date(s):
    """Whatever date format a source used -> ISO 8601 UTC, which AltStore, Feather and ESign all parse."""
    s = str(s or "").strip()
    ymd = re.match(r"(\d{4})\D(\d{1,2})\D(\d{1,2})", s)
    for parse in (lambda: datetime.datetime.fromisoformat(s.replace("Z", "+00:00")),
                  lambda: email.utils.parsedate_to_datetime(s),
                  lambda: datetime.datetime(*map(int, ymd.groups())),
                  lambda: datetime.datetime.fromtimestamp(float(s), datetime.timezone.utc)):
        try:
            d = parse()
            return (d.astimezone(datetime.timezone.utc) if d.tzinfo else d).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            pass
    return TODAY


def versions(a):
    vs = a.get("versions") if isinstance(a.get("versions"), list) else []
    if not vs:  # legacy / ESign entry: single build at the top level
        vs = [{"version": a.get("version"), "date": a.get("versionDate"), "size": a.get("size"),
               "downloadURL": a.get("downloadURL") or a.get("down"),
               "localizedDescription": a.get("versionDescription")}]
    out = []
    for v in vs:
        if not isinstance(v, dict) or not (link := url(v.get("downloadURL"))):
            continue
        o = {"version": str(v.get("version") or a.get("version") or "1.0"),
             "date": date(v.get("date") or a.get("versionDate")), "size": as_int(v.get("size")), "downloadURL": link}
        for k in ("buildVersion", "localizedDescription", "minOSVersion", "maxOSVersion"):
            if v.get(k) is not None:
                o[k] = str(v[k])
        out.append(o)
    return out


def screenshots(a):
    s = a.get("screenshotURLs") or a.get("screenshots") or []
    if isinstance(s, dict):
        s = s.get("iphone") or s.get("ipad") or []
    s = [x.get("imageURL") if isinstance(x, dict) else x for x in s] if isinstance(s, list) else []
    return [u for u in map(url, s) if u]


def merge(sources):
    apps, seen, report = [], set(), []
    for src, d in sources:
        if isinstance(d, str):
            report.append(f"FAIL {src}  ({d})")
            continue
        name, before = str(d.get("name") or src), len(apps)
        for a in d["apps"]:
            if not isinstance(a, dict) or not a.get("name") or not (a.get("bundleIdentifier") or a.get("bundleID")):
                continue
            icon = url(a.get("iconURL") or a.get("icon"))
            if str(a.get("marketplaceID") or "").strip() or not icon:  # AltStore PAL listings can't be sideloaded
                continue
            vs = [v for v in versions(a) if v["downloadURL"] not in seen]
            if not vs:
                continue
            seen.update(v["downloadURL"] for v in vs)
            bundle = str(a.get("bundleIdentifier") or a.get("bundleID"))
            desc = str(a.get("localizedDescription") or vs[0].get("localizedDescription") or a.get("subtitle") or "")
            e = {"name": str(a["name"]), "bundleIdentifier": bundle, "bundleID": bundle,
                 "developerName": str(a.get("developerName") or name),
                 "localizedDescription": f"{desc}\n\nVia {name}".strip(), "iconURL": icon, "icon": icon,
                 "version": vs[0]["version"], "versionDate": vs[0]["date"], "size": vs[0]["size"],
                 "downloadURL": vs[0]["downloadURL"], "versions": vs}
            for k in ("subtitle", "tintColor", "category"):
                if a.get(k) is not None:
                    e[k] = str(a[k])
            if shots := screenshots(a):
                e["screenshotURLs"] = shots
            if isinstance(a.get("appPermissions"), dict):
                e["appPermissions"] = a["appPermissions"]
            apps.append(e)
        report.append(f"{len(apps) - before:6}  {name}  <{src}>")
    return apps, report


def check(apps):
    """The rules Feather/KravaSign enforce on every app; one failure there rejects the whole repo."""
    assert date("2024-06.15") == "2024-06-15T00:00:00Z" and date("Fri, 24 Oct 2026 03:00:27 GMT") == "2026-10-24T03:00:27Z"
    assert url("https://a.b/x y.ipa") == "https://a.b/x%20y.ipa" and url("") == ""
    for a in apps:
        assert URL_OK.fullmatch(a["iconURL"]) and URL_OK.fullmatch(a["downloadURL"]), a["name"]
        assert DATE_OK.fullmatch(a["versionDate"]) and "marketplaceID" not in a, a["name"]
        assert all(isinstance(a[k], str) for k in ("name", "bundleIdentifier", "developerName", "version")), a["name"]
        assert all(URL_OK.fullmatch(u) for u in a.get("screenshotURLs", [])), a["name"]
        assert all(URL_OK.fullmatch(v["downloadURL"]) and DATE_OK.fullmatch(v["date"]) and isinstance(v["version"], str)
                   for v in a["versions"]), a["name"]


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
