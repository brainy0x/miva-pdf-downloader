"""Download all PDFs from your Miva (Moodle) courses into a local folder.

Usage:  python download_pdfs.py            shows your courses, you pick which ones
        python download_pdfs.py all        every course you are enrolled in
        python download_pdfs.py 566 570    specific course ids
        (566 = the id in https://lms.miva.university/course/view.php?id=566)

A browser opens. If you are not logged in, log in there; the script notices
and continues by itself. (Miva forgets the login when the browser closes, so
expect to log in on every run.)
PDFs go to ./downloads/<course>/<week>/. Re-running only fetches what is new.
Progress is printed as it goes, and problems are labelled NETWORK, LOGIN or SERVER.
Links that are not PDFs (videos, quizzes, ...) are listed in external_links.txt.
"""
import html as htmllib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

from playwright.sync_api import Error as PWError, sync_playwright

BASE = "https://lms.miva.university"
HOST = urlparse(BASE).netloc
HERE = Path(__file__).parent
PROFILE = HERE / "browser_profile"
DELAY = 3.0  # seconds between requests, be polite (Miva slows down fast clients)
BACKOFF = 60  # seconds to rest after Miva times out

NET_HINTS = (
    "ERR_INTERNET_DISCONNECTED", "ERR_NAME_NOT_RESOLVED", "ERR_CONNECTION",
    "ERR_NETWORK", "ERR_TIMED_OUT", "Timeout", "getaddrinfo", "ECONN", "ENOTFOUND",
    "socket hang up",
)
ADVICE = {
    "network": "NETWORK problem: check your internet/Wi-Fi, then run again (finished files are skipped).",
    "login": "LOGIN problem: your Miva session expired or you lack access. Run again and log in in the browser.",
    "server": "SERVER problem: Miva itself is erroring (not you). Try again later.",
    "other": "Unexpected problem, see the message above.",
}


class Fatal(Exception):
    def __init__(self, kind, msg):
        super().__init__(msg)
        self.kind = kind


def log(msg=""):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip()[:120] or "untitled"


def clean_title(t: str) -> str:
    """Drop the hidden type word Moodle appends to activity names (' URL', ' File', ...)."""
    return re.sub(r"\s+(URL|File|Page|Folder)$", "", " ".join(t.split()))


def is_network_error(e) -> bool:
    return any(h in str(e) for h in NET_HINTS)


def logged_in(page) -> bool:
    if urlparse(page.url).netloc != HOST or "/login/" in page.url:
        return False
    try:
        return not page.evaluate("document.body.classList.contains('notloggedin')")
    except PWError:
        return False  # page was mid-navigation


def open_course(page, url):
    def go():
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except PWError as e:
            if is_network_error(e):
                raise Fatal("network", f"Could not reach {HOST}: {str(e).splitlines()[0]}")
            raise

    log(f"Opening {url}")
    go()
    if logged_in(page):
        log("Already logged in.")
    else:
        log("NOT logged in. Please log in in the browser window. "
            "I will continue automatically once you are in (waiting up to 10 min).")
        deadline, last = time.time() + 600, 0.0
        while not logged_in(page):
            if time.time() > deadline:
                raise Fatal("login", "Timed out waiting for you to log in.")
            if time.time() - last >= 15:
                log("   still waiting for login...")
                last = time.time()
            page.wait_for_timeout(1000)
        log("Login detected.")
        go()
        if not logged_in(page):
            raise Fatal("login", "Logged in, but the course page still asks for login (no access to this course?).")
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PWError:
        pass


def fetch(api, url, tries=3):
    """GET with retries. Returns (response, None) or (None, (kind, detail))."""
    last = ("other", "unknown")
    for attempt in range(1, tries + 1):
        try:
            r = api.get(url, timeout=30000)
        except PWError as e:
            last = ("network" if is_network_error(e) else "other", str(e).splitlines()[0])
        else:
            if "/login/" in r.url or r.status in (401, 403):
                return None, ("login", f"HTTP {r.status} (sent to {r.url})")
            if r.status >= 500:
                last = ("server", f"HTTP {r.status}")
            elif not r.ok:
                return None, ("other", f"HTTP {r.status}")
            else:
                return r, None
        if attempt < tries:
            wait = 5 * attempt
            log(f"   {last[0].upper()} trouble ({last[1]}), retrying in {wait}s [{attempt}/{tries - 1}]")
            time.sleep(wait)
    return None, last


def resolve_url_activity(api, link, tries=3):
    """Find where a Moodle 'URL' activity points, WITHOUT visiting the outside site.

    Returns (target_url, None) or (None, (kind, detail)). Retries slow/failed lookups.
    """
    err = ("other", "unknown")
    for attempt in range(1, tries + 1):
        time.sleep(DELAY)
        target, err = lookup_url_activity(api, link)
        if not err or err[0] not in ("network", "server"):
            return target, err
        if attempt < tries:
            wait = 10 * attempt
            log(f"   {err[0].upper()} trouble ({err[1]}), retrying in {wait}s [{attempt}/{tries - 1}]")
            time.sleep(wait)
    return None, err


def lookup_url_activity(api, link):
    try:
        r = api.get(link, max_redirects=0, timeout=20000)
    except PWError as e:
        return None, ("network" if is_network_error(e) else "other", str(e).splitlines()[0])
    if r.status in (301, 302, 303, 307, 308):
        loc = urljoin(link, r.headers.get("location", ""))
        if "/login/" in loc:
            return None, ("login", "sent to the login page")
        return loc, None
    if r.status in (401, 403):
        return None, ("login", f"HTTP {r.status}")
    if r.status >= 500:
        return None, ("server", f"HTTP {r.status}")
    if not r.ok:
        return None, ("other", f"HTTP {r.status}")
    # Link shown on the page instead of redirecting.
    page_html = r.text()
    m = (re.search(r'urlworkaround.*?href="([^"]+)"', page_html, re.S)
         or re.search(r'<iframe[^>]+src="([^"]+)"', page_html)
         or re.search(r'<object[^>]+data="([^"]+)"', page_html))
    if m:
        return urljoin(link, htmllib.unescape(m.group(1))), None
    return None, ("other", "could not find the link target on the page")


def classify(target: str):
    """Return ('pdf'|'maybe'|'other', url_to_use)."""
    u = urlparse(target)
    m = re.search(r"drive\.google\.com/file/(?:u/\d+/)?d/([\w-]+)", target)
    if not m and "drive.google.com" in u.netloc:
        m = re.search(r"[?&]id=([\w-]+)", target)
    if m:
        return "pdf", f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    m = re.search(r"docs\.google\.com/(document|presentation)/d/([\w-]+)", target)
    if m:
        kind, fid = m.groups()
        export = "export?format=pdf" if kind == "document" else "export/pdf"
        return "pdf", f"https://docs.google.com/{kind}/d/{fid}/{export}"
    if u.path.lower().endswith(".pdf"):
        return "pdf", target
    if u.netloc == HOST and "/pluginfile.php/" in u.path:
        return "pdf", target  # Moodle file; verified by its %PDF header after download
    if u.netloc.endswith("miva.university") and u.netloc != HOST:
        return "maybe", target  # Miva-hosted file, may or may not be a PDF: try it
    return "other", target


def resolve_files(api, link):
    """For a resource/folder link return (list of (url, filename), other_file_count, err)."""
    time.sleep(DELAY)
    r, err = fetch(api, link)
    if err:
        return [], 0, err
    ctype = r.headers.get("content-type", "").lower()
    if "pdf" in ctype:
        return [(r.url, safe(unquote(Path(urlparse(r.url).path).name)))], 0, None
    if "text/html" not in ctype:
        return [], 1, None  # pptx/docx/etc.
    urls = dict.fromkeys(
        u.replace("&amp;", "&")
        for u in re.findall(r'https?://[^"\'<>\s]+/pluginfile\.php/[^"\'<>\s]+', r.text())
        if "/mod_resource/" in u or "/mod_folder/" in u
    )
    pdfs = [u.split("?")[0] for u in urls if urlparse(u).path.lower().endswith(".pdf")]
    pdfs = list(dict.fromkeys(pdfs))
    return [(u, safe(unquote(Path(urlparse(u).path).name))) for u in pdfs], len(urls) - len(pdfs), None


COURSE_LIST_JS = """async () => {
    const key = window.M && M.cfg && M.cfg.sesskey;
    const r = await fetch('/lib/ajax/service.php?sesskey=' + key +
        '&info=core_course_get_enrolled_courses_by_timeline_classification', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify([{index: 0,
            methodname: 'core_course_get_enrolled_courses_by_timeline_classification',
            args: {offset: 0, limit: 0, classification: 'all', sort: 'fullname',
                   customfieldname: '', customfieldvalue: ''}}])
    });
    const data = await r.json();
    return data[0].data.courses.map(c => [String(c.id), c.fullname]);
}"""

COURSE_LINKS_JS = """() => [...document.querySelectorAll('a[href*="/course/view.php?id="]')]
    .map(a => [new URL(a.href).searchParams.get('id'), a.innerText.trim()])"""


def list_courses(page):
    """Return [(course_id, name), ...] for the logged-in student, or [] if it cannot be read."""
    open_course(page, f"{BASE}/my/courses.php")
    found = []
    try:
        found = page.evaluate(COURSE_LIST_JS)
    except PWError:
        pass
    if not found:  # fall back to reading the course cards on the page
        names = {}
        try:
            for cid, text in page.evaluate(COURSE_LINKS_JS):
                if cid and len(text) > len(names.get(cid, "")):
                    names[cid] = text
        except PWError:
            pass
        found = list(names.items())
    return [(cid, " ".join(name.split()) or f"course {cid}") for cid, name in found]


def parse_choice(answer: str, count: int):
    """'1 3' / '1,3' / 'all' -> list of 0-based indexes; None if the answer makes no sense."""
    answer = answer.strip().lower()
    if answer in ("all", "a"):
        return list(range(count))
    picks = []
    for part in re.split(r"[\s,]+", answer):
        if not part.isdigit() or not 1 <= int(part) <= count:
            return None
        if int(part) - 1 not in picks:
            picks.append(int(part) - 1)
    return picks or None


def choose_courses(courses):
    print()
    for i, (cid, name) in enumerate(courses, 1):
        print(f"  {i:>2}. {name}")
    print()
    while True:
        picks = parse_choice(input("Type the numbers of the courses to download (e.g. 1 3), or 'all': "), len(courses))
        if picks is not None:
            return [courses[i][0] for i in picks]
        print(f"   Please type numbers between 1 and {len(courses)}, separated by spaces, or the word all.")


def run_course(ctx, course_id: str):
    out_root = HERE / "downloads"
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    open_course(page, f"{BASE}/course/view.php?id={course_id}")

    course_title = safe(page.title().split(":")[-1].split("|")[0])
    out_dir = out_root / f"{course_id}_{course_title}"
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"Course: {course_title}")

    # Expand every collapsed section so all activities are in the DOM.
    for btn in page.query_selector_all('[data-action="toggle-all-sections"], a.collapsed[data-toggle="collapse"]'):
        try:
            btn.click(timeout=500)
        except Exception:
            pass

    sections = page.evaluate(
        """() => [...document.querySelectorAll('li.section, section.course-section')].map(s => {
            const byHref = new Map();
            s.querySelectorAll('a[href*="/mod/resource/view.php"], a[href*="/mod/folder/view.php"], a[href*="/mod/url/view.php"]')
                .forEach(a => {
                    const text = a.innerText.trim();
                    if (!byHref.has(a.href) || (!byHref.get(a.href) && text)) byHref.set(a.href, text);
                });
            return {
                title: (s.querySelector('.sectionname, h3')?.innerText || 'Section').trim(),
                links: [...byHref].map(([href, text]) => ({href, text}))
            };
        })"""
    )
    activities = [
        (i, sec["title"], l["href"], clean_title(l["text"]) or "item")
        for i, sec in enumerate(sections)
        for l in sec["links"]
    ]
    log(f"Found {len(sections)} sections containing {len(activities)} file/folder/link items.")
    if not activities:
        dbg = HERE / f"debug_course_{course_id}.html"
        dbg.write_text(page.content(), encoding="utf-8")
        log(f"Nothing to download. Saved the page to {dbg.name} so the selectors can be fixed - send it to Claude.")
        return

    api = ctx.request
    cache_file = out_dir / "known_links.json"
    try:
        known = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        known = {}  # link -> [section, title, target] for items already found to be non-PDFs
    seen, saved, skipped, other_files, failed = set(), 0, 0, 0, []
    bad_streak = 0
    total = len(activities)
    n = 0

    def failure(what, err):
        nonlocal bad_streak
        failed.append((what, err))
        log(f"   FAILED: {err[0].upper()} - {err[1]}")
        bad_streak += 1
        if bad_streak >= (3 if err[0] == "login" else 4) and err[0] in ("network", "login"):
            raise Fatal(err[0], f"{bad_streak} items in a row failed.")
        if err[0] in ("network", "server"):
            log(f"   Miva is slow or not answering - resting {BACKOFF}s before the next item...")
            time.sleep(BACKOFF)

    try:
        for n, (idx, sec_title, link, title) in enumerate(activities, 1):
            log(f"[{n}/{total}] {title}")
            sec_dir = out_dir / f"{idx:02d}_{safe(sec_title)}"
            if "/mod/url/" in link:
                dest = sec_dir / (safe(title) + ".pdf")
                if dest.exists():  # no need to bother Miva about it
                    skipped += 1
                    log("   already have it")
                    continue
                if link in known:
                    log(f"   known non-PDF ({urlparse(known[link][2]).netloc}), skipped")
                    continue
                target, err = resolve_url_activity(api, link)
                if err:
                    failure(title, err)
                    continue
                kind, direct = classify(target)
                if kind == "other":
                    bad_streak = 0
                    known[link] = [sec_title, title, target]
                    log(f"   not a PDF, skipped ({urlparse(target).netloc})")
                    continue
                files, others = [(direct, dest.name, kind == "maybe")], 0
            else:
                files, others, err = resolve_files(api, link)
                if err:
                    failure(title, err)
                    continue
                files = [(u, f, False) for u, f in files]
            other_files += others
            bad_streak = 0
            if not files:
                log("   no PDF in this item")
            for file_url, fname, maybe in files:
                if file_url in seen:
                    log("   same file as an earlier item, skipped")
                    continue
                seen.add(file_url)
                dest = sec_dir / fname
                if dest.exists():
                    skipped += 1
                    log(f"   already have {dest.name}")
                    continue
                if maybe:  # peek at the headers first so we never download a big non-PDF package
                    try:
                        h = api.head(file_url, timeout=15000)
                        checked, is_pdf = True, h.ok and "pdf" in h.headers.get("content-type", "").lower()
                    except PWError:
                        checked, is_pdf = False, False
                    if not is_pdf:
                        if checked:
                            known[link] = [sec_title, title, file_url]
                        log(f"   not a PDF{'' if checked else ' (could not check, will retry next run)'}, "
                            f"skipped ({urlparse(file_url).netloc})")
                        continue
                r, err = fetch(api, file_url)
                body = r.body() if r else b""
                if not err and not body.startswith(b"%PDF"):
                    if maybe:
                        bad_streak = 0
                        known[link] = [sec_title, title, file_url]
                        log(f"   not a PDF, skipped ({urlparse(file_url).netloc})")
                        continue
                    if "google.com" in file_url:
                        err = ("login", "Google Drive wants a Google login. In this browser window open drive.google.com "
                                        "in a new tab, sign in with the Google account you use for Miva, then run again")
                    else:
                        err = ("login", "got a web page instead of a PDF (needs a login there, or not a direct file link)")
                if err:
                    failure(fname, err)
                    continue
                bad_streak = 0
                sec_dir.mkdir(exist_ok=True)
                dest.write_bytes(body)
                saved += 1
                log(f"   saved {dest.name} ({len(body) // 1024} KB)")
                time.sleep(DELAY)
    finally:
        cache_file.write_text(json.dumps(known, indent=1), encoding="utf-8")
        if known:
            (out_dir / "external_links.txt").write_text(
                "\n".join(f"{s} | {t} | {u}" for s, t, u in known.values()), encoding="utf-8")
        log("=" * 50)
        if n < total:
            log(f"Stopped at item {n} of {total}; the rest were not checked.")
        log(f"Summary: {saved} new PDFs, {skipped} already downloaded, {len(failed)} failed.")
        if known:
            log(f"{len(known)} links were not PDFs (videos, forms, ...). Listed in external_links.txt")
        if other_files:
            log(f"{other_files} non-PDF files (pptx/docx/...) were found and not downloaded.")
        for what, (kind, detail) in failed:
            log(f"   FAILED {what}: {kind.upper()} - {detail}")
        for kind in dict.fromkeys(k for _, (k, _) in failed):
            log(ADVICE[kind])
        log(f"Files are in: {out_dir}")


def launch_browser(p):
    """Use an installed Chrome/Edge if possible; only download Chromium as a last resort."""
    def attempt(channel=None, exe=None):
        kw = {}
        if channel:
            kw["channel"] = channel
        if exe:
            kw["executable_path"] = exe
        return p.chromium.launch_persistent_context(str(PROFILE), headless=False, **kw)

    for channel in ("chrome", "msedge", "chrome-beta", "msedge-beta", "chrome-dev", "msedge-dev"):
        try:
            return attempt(channel)
        except PWError:
            pass
    # Other Chromium-based browsers (not officially supported by Playwright, but usually work)
    roots = [os.environ.get(k) for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
    forks = [
        r"BraveSoftware\Brave-Browser\Application\brave.exe",
        r"Vivaldi\Application\vivaldi.exe",
        r"Programs\Opera\opera.exe",
        r"Opera\opera.exe",
        r"Chromium\Application\chrome.exe",
    ]
    for root in filter(None, roots):
        for rel in forks:
            exe = Path(root) / rel
            if exe.exists():
                try:
                    return attempt(exe=str(exe))
                except PWError:
                    pass
    try:
        return attempt()
    except PWError:
        log("No Chrome/Edge found. Downloading a browser (one time, takes a few minutes)...")
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
        return attempt()


def main(args) -> None:
    ids = [a for a in args if a.isdigit()]
    with sync_playwright() as p:
        log("Starting browser... (a new window should appear; check the taskbar if you don't see it)")
        ctx = launch_browser(p)
        try:
            if not ids:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                courses = list_courses(page)
                if not courses:
                    log("Could not read your course list. Run again with a course id, e.g.  python download_pdfs.py 566")
                    return
                log(f"You are enrolled in {len(courses)} courses.")
                ids = [c for c, _ in courses] if "all" in args else choose_courses(courses)
            for n, cid in enumerate(ids, 1):
                log(f"===== Course id {cid} ({n} of {len(ids)}) =====")
                run_course(ctx, cid)
        except Fatal as e:
            log(f"STOPPED: {e}")
            log(ADVICE[e.kind])
        except PWError as e:
            if "closed" in str(e).lower():
                log("STOPPED: the browser window was closed.")
            else:
                log(f"STOPPED: unexpected browser error: {str(e).splitlines()[0]}")
        except KeyboardInterrupt:
            log("Stopped by you (Ctrl+C). Re-run to continue; finished files are skipped.")
        finally:
            try:
                ctx.close()
            except Exception:
                pass


if __name__ == "__main__":
    main(sys.argv[1:])
