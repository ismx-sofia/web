#!/usr/bin/env python3
"""Contract of the SofIA website build. Run: python3 tools/test_build.py

Black-box: builds into a temporary directory and asserts on the generated files, the way GitHub Pages
will serve them. Set SOFIA_MONOREPO=/path/to/03-code to also check the deep-link snapshot against
the app's DeeplinkBusiness.ts.
"""
from __future__ import annotations

import base64
import hashlib
import html.parser
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import build  # noqa: E402
import documents  # noqa: E402

SRC = ROOT / "src"
HOST = build.HOST
LOC = json.loads((SRC / "data" / "locales.json").read_text(encoding="utf-8"))
PUBLISHED = [LOC["locales"][c]["file"] for c in LOC["order"]] + [LOC["locales"]["pt"]["variant"]["file"]]
TOKENS = re.compile(r"\{\{(?:url):[^}]+\}\}|\{(?:path|label):[^}]+\}|<[^>]+>")
APP_URLS = ["/privacy", "/terms", "/faq", "/ia", "/legal"]  # opened by the app (LegalSettingsScreen, SettingsScreen, IntelligenceSettingsScreen)
AASA_PATTERNS = [r"^/user/", r"^/entry/", r"^/reader", r"^/journal", r"^/settings/", r"^/activity", r"^/notifications", r"^/search", r"^/chat"]


class Links(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs: list[tuple[str, str, str]] = []  # (tag, attr, value)
        self.ids: set[str] = set()

    def handle_starttag(self, tag, attrs):
        for k, v in attrs:
            if k in ("href", "src") and v is not None:
                self.refs.append((tag, k, v))
            if k == "id":
                self.ids.add(v)


def parse(text: str) -> Links:
    p = Links()
    p.feed(text)
    return p


def resolve(out: Path, url: str) -> Path | None:
    path = url.split("#")[0].split("?")[0]
    if path in ("", "/"):
        return out / "index.html"
    target = out / path.lstrip("/")
    if path.endswith("/"):
        return target / "index.html"
    if target.is_file():
        return target
    if Path(str(target) + ".html").is_file():
        return Path(str(target) + ".html")
    return None


class BuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "docs"
        site = build.Site(release=False)
        cls.info = site.build(cls.out)
        cls.site = site
        cls.pages = {p.relative_to(cls.out).as_posix(): p.read_text(encoding="utf-8") for p in cls.out.rglob("*.html")}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    # 1. every key a template uses exists in every published dictionary, and none is left over
    def test_keys_complete_and_no_leftovers(self):
        used = set()
        for f in list((SRC / "pages").glob("*.html")) + list((SRC / "templates").glob("*.html")):
            used |= set(re.findall(r"\{\{[ta]:([^}]+)\}\}", f.read_text(encoding="utf-8")))
        used |= {f"aud.{n}.{k}" for n in range(4) for k in ("chip", "who", "text")}
        used |= {"suggest.text", "suggest.go", "suggest.close", "legal.langnote", "legal.translated"}
        used |= {f"legal.{k}" for k in build.Site.LEGAL_WORDS}  # the frame of a translated legal text
        for f in (SRC / "pages").glob("*.html"):
            meta, _ = build.page_meta(f.read_text(encoding="utf-8"))
            used |= {meta["title-key"], meta["description-key"]}
        for name in PUBLISHED:
            d = json.loads((SRC / "i18n" / f"{name}.json").read_text(encoding="utf-8"))
            self.assertEqual(sorted(used - set(d)), [], f"{name}: missing keys")
            self.assertEqual(sorted(set(d) - used), [], f"{name}: keys no template uses")

    # 2. markup and tokens survive translation
    def test_translations_keep_markup_and_tokens(self):
        es = json.loads((SRC / "i18n" / "es.json").read_text(encoding="utf-8"))
        for name in PUBLISHED:
            d = json.loads((SRC / "i18n" / f"{name}.json").read_text(encoding="utf-8"))
            for k, v in es.items():
                self.assertEqual(sorted(TOKENS.findall(v)), sorted(TOKENS.findall(d[k])), f"{name} {k}")
                self.assertTrue(d[k].strip(), f"{name} {k} empty")

    # 3. every app path names labels that exist
    def test_app_paths_known(self):
        labels = json.loads((SRC / "data" / "app-labels.json").read_text(encoding="utf-8"))["labels"]
        blob = "".join(f.read_text(encoding="utf-8") for f in (SRC / "i18n").glob("*.json"))
        blob += "".join(f.read_text(encoding="utf-8") for f in (SRC / "pages").glob("*.html"))
        ids = set()
        for m in re.findall(r"\{path:([^}]+)\}|\{label:([^}]+)\}|\{\{steps:([^}]+)\}\}", blob):
            for g in m:
                ids |= set(filter(None, g.split(",")))
        self.assertTrue(ids)
        self.assertEqual(sorted(ids - set(labels)), [])
        for page, text in self.pages.items():
            self.assertNotRegex(text, r"\{path:|\{label:|\{\{[a-z]+:", page)

    # 4. internal links and assets resolve to a generated file
    def test_internal_links_resolve(self):
        # A frozen version keeps what it linked then: its links are a record, not the live site
        # (/sensitive left the site when each consent got its own page, 2026-10-18).
        frozen = set()
        for doc in json.loads((SRC / "data" / "documents.json").read_text(encoding="utf-8"))["documents"]:
            for v in doc["versions"]:
                for code, f in v["files"].items():
                    d = LOC["locales"][code]["dir"]
                    frozen.add((f"{d}/" if d else "") + f"documents/{doc['web']}/original/{v['version']}.html")
                    if f.get("legacy"):
                        frozen.add(f["legacy"])
        for page, text in self.pages.items():
            if page in frozen:
                continue
            for tag, attr, url in parse(text).refs:
                if url.startswith(HOST):
                    url = url[len(HOST):] or "/"
                if url.startswith("/") and not url.startswith("//"):
                    self.assertIsNotNone(resolve(self.out, url), f"{page}: {tag} {attr}={url}")

    # 4b. in-page anchors exist (TOC, FAQ, #funciones…)
    def test_anchors_resolve(self):
        frozen = set()
        for doc in json.loads((SRC / "data" / "documents.json").read_text(encoding="utf-8"))["documents"]:
            for v in doc["versions"]:
                for code, f in v["files"].items():
                    d = LOC["locales"][code]["dir"]
                    frozen.add((f"{d}/" if d else "") + f"documents/{doc['web']}/original/{v['version']}.html")
                    if f.get("legacy"):
                        frozen.add(f["legacy"])
        for page, text in self.pages.items():
            if page in frozen:
                continue
            p = parse(text)
            for tag, attr, url in p.refs:
                if tag == "a" and "#" in url and not url.startswith(("http", "mailto")):
                    base, frag = url.split("#", 1)
                    if not frag:
                        continue
                    target = page if not base else resolve(self.out, base).relative_to(self.out).as_posix()
                    self.assertIn(frag, parse(self.pages[target]).ids, f"{page}: {url}")

    # 5. hreflang reciprocal with x-default, canonical to itself
    def test_hreflang_and_canonical(self):
        expected = {LOC["locales"][c]["hreflang"] for c in LOC["order"]} | {"x-default"}
        for page in build.LOCALIZED_PAGES:
            for c in LOC["order"]:
                rel = self.site.out_path(page, c)
                text = self.pages[rel]
                alts = dict(re.findall(r'<link rel="alternate" hreflang="([^"]+)" href="([^"]+)">', text))
                self.assertEqual(set(alts), expected, rel)
                canon = re.search(r'<link rel="canonical" href="([^"]+)">', text).group(1)
                self.assertEqual(canon, HOST + self.site.url(page, c), rel)
                self.assertEqual(alts[LOC["locales"][c]["hreflang"]], canon, rel)

    # 5b. the pt-PT switch covers every translatable node of /pt/
    def test_variant_dict_complete(self):
        for page in build.LOCALIZED_PAGES:
            text = self.pages[self.site.out_path(page, "pt")]
            data = json.loads(re.search(r"window\.SOFIA=(.*?);</script>", text).group(1))
            keys = set(re.findall(r'data-i18n(?:-aria|-title)?="([^"]+)"', text))
            self.assertEqual(sorted(keys - set(data["variant"]["dict"])), [], page)

    # 6. the 404 router is DeeplinkBusiness.routes minus webless
    def test_router_matches_app_routes(self):
        snap = json.loads((SRC / "data" / "routes.json").read_text(encoding="utf-8"))
        data = json.loads(re.search(r"window\.SOFIA=(.*?);</script>", self.pages["404.html"]).group(1))
        self.assertEqual([r["path"] for r in data["router"]], [r["path"] for r in snap["routes"]], "order matters")
        self.assertEqual([r["path"] for r in data["router"] if not r["web"]], [r["path"] for r in snap["routes"] if r["path"] in snap["webless"]])
        def known(path, query=""):
            # mirror of site.js: drop empty segments, first matching route wins
            path = re.sub(r"/{2,}", "/", path).rstrip("/")
            q = dict(x.split("=") for x in query.split("&") if x)
            for r in data["router"]:
                m = re.match(r["re"].replace("(?<", "(?P<"), path)
                if m:
                    return r["web"] and (all(m.groupdict().get(n) or q.get(n) for n in r["requires"])
                                         or bool(r.get("code") and q.get("code")))
            return False
        self.assertTrue(known("/entry", "code=K7Q2M9X"))    # a shared link: only its code
        self.assertTrue(known("/user", "code=K7Q2M9X"))
        self.assertTrue(known("/entry/abc"))
        self.assertTrue(known("/entry", "id=abc"))          # links handed out before the path form
        self.assertTrue(known("/entry/create/call"))
        self.assertTrue(known("/settings/activity/target/create"))
        self.assertFalse(known("/entry"))                   # entry needs an id or a code
        self.assertFalse(known("/recovery"))                # webless
        self.assertFalse(known("/privacy-old"))
        self.assertFalse(known("/entry/quick"))             # the Quick Journal, not the entry «quick»
        self.assertFalse(known("/entry/inbox/x"))           # webless
        self.assertTrue(known("/entry//abc"))               # the app drops empty segments too

    def test_routes_snapshot_matches_app(self):
        mono = os.environ.get("SOFIA_MONOREPO")
        if not mono:
            self.skipTest("SOFIA_MONOREPO not set")
        s = Path(mono, "apps/mobile/src/business/device/os/DeeplinkBusiness.ts").read_text(encoding="utf-8")
        app = re.findall(r"\{\s*path:\s*'([^']+)'", s)
        snap = [r["path"] for r in json.loads((SRC / "data" / "routes.json").read_text(encoding="utf-8"))["routes"]]
        self.assertEqual(app, snap, "DeeplinkBusiness.routes changed: update src/data/routes.json")

    # 7. what the audit found false never comes back
    def test_no_false_or_placeholder_claims(self):
        # «suscripci» left this list with SofIA Plus, Pro and Max (Condiciones de la suscripción, 13-10).
        bad = ["Lorem", "[RELLENAR", "Gemini", "AWS", "Legado Seguro", "Asistente Conversacional",
               "funciones de pago", "no te cobra nada", "Algunas pantallas de la app anuncian"]
        frozen = set()
        for doc in json.loads((SRC / "data" / "documents.json").read_text(encoding="utf-8"))["documents"]:
            for v in doc["versions"]:
                for code, f in v["files"].items():
                    d = LOC["locales"][code]["dir"]
                    frozen.add((f"{d}/" if d else "") + f"documents/{doc['web']}/original/{v['version']}.html")
                    if f.get("legacy"):
                        frozen.add(f["legacy"])
        for page, text in self.pages.items():
            if page in frozen:  # a frozen version keeps what it said then
                continue
            for b in bad:
                self.assertNotIn(b, text, f"{page}: {b}")
            for m in re.finditer(r"extremo a extremo", text):
                self.assertIn("no es", text[max(0, m.start() - 40):m.start()].lower(), f"{page}: end-to-end claim")

    # 8. the URLs the app opens exist, and /faq keeps #seguridad
    def test_app_urls_exist(self):
        for u in APP_URLS:
            self.assertIsNotNone(resolve(self.out, u), u)
        self.assertIn("seguridad", parse(self.pages["faq.html"]).ids)

    # 9. no third parties: every script, stylesheet, font and image is ours
    def test_no_third_party_resources(self):
        for page, text in self.pages.items():
            for tag, attr, url in parse(text).refs:
                if tag in ("script", "img", "link", "source", "iframe") and url.startswith(("http:", "https:", "//")):
                    self.assertTrue(url.startswith(HOST), f"{page}: {tag} {url}")
        css = (self.out / "assets" / "site.css").read_text(encoding="utf-8")
        self.assertNotRegex(css, r"url\(\s*['\"]?(https?:)?//")

    # 10. a page never shares its name with a folder (Pages behaviour for /x is undefined then)
    def test_no_file_and_folder_with_same_name(self):
        for p in self.out.rglob("*.html"):
            self.assertFalse(p.with_suffix("").is_dir(), p)

    # 11. static files are copied byte for byte
    def test_static_files_untouched(self):
        for f in (SRC / "static").rglob("*"):
            if f.is_file():
                rel = f.relative_to(SRC / "static")
                self.assertEqual((self.out / rel).read_bytes(), f.read_bytes(), rel)
        for must in ("CNAME", ".nojekyll", ".well-known/apple-app-site-association", ".well-known/assetlinks.json", "app-ads.txt"):
            self.assertTrue((self.out / must).is_file(), must)

    # 12. /documents: every frozen original is served byte for byte at its /documents URL and, if it was
    # published before /documents, at its old URL too; its sha256 is the one the registry (and the database) holds
    def test_documents_originals_are_frozen(self):
        registry = json.loads((SRC / "data" / "documents.json").read_text(encoding="utf-8"))["documents"]
        self.assertTrue(registry)
        for doc in registry:
            for v in doc["versions"]:
                for code, f in v["files"].items():
                    d = LOC["locales"][code]["dir"]
                    served = self.out / (f"{d}/" if d else "") / "documents" / doc["web"] / "original" / f"{v['version']}.html"
                    self.assertEqual(hashlib.sha256(served.read_bytes()).hexdigest(), f["sha256"], served)
                    if f.get("legacy"):
                        self.assertEqual((self.out / f["legacy"]).read_bytes(), served.read_bytes(), f["legacy"])

    # 12a. a legal page in force says since when it applies, never a version number: the number is the URL's
    # (Israel, 22-10-2026: «no es necesario poner el código interno de la versión»); the frozen originals keep theirs
    def test_legal_pages_show_no_version(self):
        registry = json.loads((SRC / "data" / "documents.json").read_text(encoding="utf-8"))["documents"]
        frozen = {f["legacy"] for d in registry for v in d["versions"] for f in v["files"].values() if f.get("legacy")}
        for name, page in self.pages.items():
            if 'class="meta-pills"' in page and "documents/" not in name and name not in frozen:
                self.assertNotRegex(page, r'class="meta-pills"><span>[^<]*\d', name)
                self.assertIn('<div class="meta-pills"><span>', page, name)

    # 12b. one page per version, framing its original; the index and each document's page list them all
    def test_documents_pages(self):
        registry = json.loads((SRC / "data" / "documents.json").read_text(encoding="utf-8"))["documents"]
        index = self.pages["documents/index.html"]
        for doc in registry:
            self.assertIn(f'href="/documents/{doc["web"]}/"', index, doc["id"])
            opener = self.pages[f"documents/{doc['web']}/index.html"]
            self.assertIn(f'data-doc-open="{doc["web"]}"', opener)
            for i, v in enumerate(doc["versions"]):
                page = self.pages[f"documents/{doc['web']}/{v['version']}.html"]
                self.assertIn(f'<iframe class="orig-frame" src="/documents/{doc["web"]}/original/{v["version"]}"', page)
                self.assertIn(f'href="/documents/{doc["web"]}/{v["version"]}"', index)
                # The code is the URL, never the page's text (Israel, 22-10-2026).
                self.assertNotIn(f">{v['version']}<", page)
                if i == 0:
                    self.assertNotIn("Qué cambia en esta versión", page, f"{doc['id']} {v['version']}: a first version has none")
                    self.assertNotIn("Requiere aceptación", page)
                    self.assertNotIn("Cambio menor", page)
                else:
                    self.assertIn("Qué cambia en esta versión", page)
                    self.assertIn("Requiere aceptación" if v["material"] else "Cambio menor", page)

    # 12c. the manifest core ingests: every version, its originals with their sha256, and «what changes»
    def test_documents_manifest(self):
        registry = json.loads((SRC / "data" / "documents.json").read_text(encoding="utf-8"))["documents"]
        m = json.loads((self.out / "documents.json").read_text(encoding="utf-8"))
        self.assertEqual(m["origin"], HOST)
        self.assertEqual([d["id"] for d in m["documents"]], [d["id"] for d in registry])
        for doc, md in zip(registry, m["documents"]):
            self.assertEqual([v["version"] for v in md["versions"]], [v["version"] for v in doc["versions"]])
            for v, mv in zip(doc["versions"], md["versions"]):
                self.assertEqual(sorted(mv["locales"]), sorted(PUBLISHED), "one entry per i18n file")
                es = mv["locales"]["es"]
                self.assertEqual(es["sha256"], v["files"]["es"]["sha256"])
                self.assertEqual(hashlib.sha256(resolve(self.out, es["url"]).read_bytes()).hexdigest(), es["sha256"])
                self.assertIsNotNone(resolve(self.out, mv["url"]), mv["url"])
                self.assertEqual(mv["locales"]["pt-BR"]["sha256"], mv["locales"]["pt-PT"]["sha256"], "one /pt/ page, both texts")
                self.assertEqual(int(datetime.fromisoformat(mv["effective"]).strftime("%y%m%d")), mv["version"])
        self.assertFalse((self.out / "documents.json.sig").exists(), "only the release build signs")

    # 12d. the registry's contract: the build refuses a frozen file that changed, a version that is not the
    # Madrid date of its effective time, two versions of a document on one day, and a release without every
    # «what changes»
    def test_documents_registry_refuses(self):
        def attempt(mutate, release=False):
            with tempfile.TemporaryDirectory() as t:
                src = Path(t)
                shutil.copytree(SRC / "documents", src / "documents")
                shutil.copytree(SRC / "legal", src / "legal")
                (src / "data").mkdir()
                reg = json.loads((SRC / "data" / "documents.json").read_text(encoding="utf-8"))
                mutate(reg, src)
                (src / "data" / "documents.json").write_text(json.dumps(reg), encoding="utf-8")
                site = build.Site(release=False)
                return documents.Documents(site, src=src, root=ROOT, release=release, host=HOST, page_meta=build.page_meta)

        attempt(lambda reg, src: None)  # the registry as it is passes

        def tamper(reg, src):
            f = src / "documents" / reg["documents"][0]["versions"][0]["files"]["es"]["path"]
            f.write_bytes(f.read_bytes() + b" ")
        def wrong_date(reg, src):
            reg["documents"][0]["versions"][-1]["version"] += 1
        def same_day(reg, src):
            vs = reg["documents"][0]["versions"]
            vs.append(dict(vs[-1]))
        def changes_on_first(reg, src):
            reg["documents"][0]["versions"][0]["changes"] = {"es": "x"}
        for name, mutate in (("tampered", tamper), ("date", wrong_date), ("same day", same_day), ("first", changes_on_first)):
            with self.subTest(name), self.assertRaises(documents.DocumentsError):
                attempt(mutate)
        incomplete = any(v["changes"] and len(v["changes"]) < len(PUBLISHED)
                         for d in json.loads((SRC / "data" / "documents.json").read_text(encoding="utf-8"))["documents"]
                         for v in d["versions"][1:])
        if incomplete:
            with self.assertRaises(documents.DocumentsError):
                attempt(lambda reg, src: None, release=True)

    # 12e. the release signs the manifest with Ed25519, and the signature verifies with the public key
    def test_documents_manifest_signature(self):
        if not shutil.which("openssl"):
            self.skipTest("openssl not installed")
        with tempfile.TemporaryDirectory() as t:
            key, pub, sig = Path(t) / "k.pem", Path(t) / "k.pub", Path(t) / "m.sig"
            subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(key)], check=True, capture_output=True)
            subprocess.run(["openssl", "pkey", "-in", str(key), "-pubout", "-out", str(pub)], check=True, capture_output=True)
            os.environ[documents.SIGNING_KEY_ENV] = str(key)
            try:
                site = build.Site(release=False)
                docs = documents.Documents(site, src=SRC, root=ROOT, release=False, host=HOST, page_meta=build.page_meta)
                signature = docs.sign(self.out / "documents.json")
            finally:
                del os.environ[documents.SIGNING_KEY_ENV]
            sig.write_bytes(base64.b64decode(signature))
            ok = subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(pub), "-rawin",
                                 "-in", str(self.out / "documents.json"), "-sigfile", str(sig)], capture_output=True)
            self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
            tampered = Path(t) / "m.json"
            tampered.write_bytes((self.out / "documents.json").read_bytes().replace(b'"material":false', b'"material":true', 1))
            bad = subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(pub), "-rawin",
                                  "-in", str(tampered), "-sigfile", str(sig)], capture_output=True)
            self.assertNotEqual(bad.returncode, 0, "a changed manifest must not verify")
        # The real public key ships beside the encrypted private one.
        self.assertTrue((ROOT / "secrets" / "documents-signing.pub.pem").is_file())

    # 13. deterministic, and docs/ is up to date with src/
    def test_deterministic_and_committed_output_current(self):
        with tempfile.TemporaryDirectory() as t:
            build.Site(release=False).build(Path(t) / "again")
            a = {p.relative_to(Path(t) / "again"): p.read_bytes() for p in (Path(t) / "again").rglob("*") if p.is_file()}
        b = {p.relative_to(self.out): p.read_bytes() for p in self.out.rglob("*") if p.is_file()}
        self.assertEqual(a.keys(), b.keys())
        self.assertTrue(all(a[k] == b[k] for k in a), "build is not deterministic")
        docs = ROOT / "docs"
        if docs.exists():
            c = {p.relative_to(docs): p.read_bytes() for p in docs.rglob("*") if p.is_file()}
            stale = sorted(str(k) for k in set(b) | set(c) if b.get(k) != c.get(k))
            self.assertEqual(stale, [], "docs/ is stale: run python3 tools/build.py")

    # 14. what the stores and the LSSI ask the site to show
    def test_store_and_lssi_requirements(self):
        for page, text in self.pages.items():
            self.assertRegex(text, r'href="/legal"', f"{page}: LSSI art. 10 link")
            self.assertIn("PROGRAMSAN, S.L.", text, page)
        for c in LOC["order"]:
            delete = self.pages[self.site.out_path("delete-account", c)]
            self.assertIn("SofIA", delete)
            self.assertIn("hello@sofia.ismx.app", delete, f"{c}: a way to ask without the app")
            self.assertRegex(delete, r"3\s?(años|years|anys|ans|anos|Jahre|anni|年)", f"{c}: retention")
            self.assertIn("hello@sofia.ismx.app", self.pages[self.site.out_path("help", c)])
            privacy = self.site.url(build.PRIVACY_BASIC, c)
            self.assertIn(f'href="{privacy}"', self.pages[self.site.out_path("index", c)], f"{c}: privacy in the footer")
            if c != "es":
                summary = self.pages[self.site.out_path(build.PRIVACY_BASIC, c)]
                self.assertIn('href="/privacy" hreflang="es"', summary, f"{c}: the summary leads to the binding text")
                self.assertIn("legal@sofia.ismx.app", summary, f"{c}: privacy contact")
                self.assertIn("B40623829", summary, f"{c}: controller")
                self.assertIn("dpo@sofia.ismx.app", summary, f"{c}: data protection officer")
        for page in ("privacy", "legal", "consent-journal", "consent-health"):
            self.assertIn("dpo@sofia.ismx.app", self.pages[f"{page}.html"], f"{page}: data protection officer")

    # 14b. the Spanish policy and its summaries point at each other, and legal tables read on a phone
    def test_privacy_summaries_and_legal_tables(self):
        full = self.pages["privacy.html"]
        for c in LOC["order"]:
            self.assertIn(f'hreflang="{LOC["locales"][c]["hreflang"]}" href="{build.HOST}{self.site.url(build.PRIVACY_BASIC, c)}"', full, c)
        self.assertFalse(Path(self.out, "privacy-basic.html").exists())
        for page in build.LEGAL_PAGES:
            text = self.pages[f"{page}.html"]
            self.assertNotIn("<td>", text, f"{page}: a cell without its column label")

    # 14c. every versioned legal text says since when it is in force, and the LSSI data are complete
    def test_legal_dates_and_lssi_data(self):
        for page in build.LEGAL_PAGES:
            path = build.SRC / "legal" / f"{page}.html"
            if not path.exists():
                continue
            meta, _ = build.page_meta(path.read_text(encoding="utf-8"))
            text = self.pages[f"{page}.html"]
            if "version" in meta and "since" not in meta:
                # A version announced but not yet dated: the draft shows it pending, the release refuses it.
                self.assertIn('Vigente desde el <b><span class="pending">', text, page)
                continue
            self.assertRegex(text, r"Vigente desde el(?:</span>)?\s*<b>\d{1,2} de [a-z]+ de \d{4}</b>", page)
            if "version" in meta:
                self.assertRegex(meta.get("since", ""), r"^\d{1,2} de [a-z]+ de \d{4}$", f"{page}: since")
        legal = self.pages["legal.html"]
        for fact in ("B40623829", "Monte Carmelo, 6, BO", "46019 Valencia", "tomo 10772", "libro 8051",
                     "folio 14", "hoja 191053", "hello@sofia.ismx.app"):
            self.assertIn(fact, legal)

    # 14d. a translated legal text: same sections, tables and figures as the Spanish one, its own
    # language in the frame, reciprocal hreflang, and a footer link from every page in every language
    def test_translated_legal(self):
        for page, codes in build.TRANSLATED_LEGAL.items():
            es = self.pages[f"{page}.html"]
            es_ids = re.findall(r'<section id="([^"]+)">\n<h2>', es)
            def figures(text):
                tables = re.findall(r"<table>.*?</table>", text, flags=re.S)
                return [re.sub(r"[.,\s\u202f]", "", n) for tb in tables for n in re.findall(r"\d[\d.,\s\u202f]*\d|\d", re.sub(r"<[^>]+>", " ", tb))]
            def prose(text):
                # deadlines, prices and counts said in the running text: the same multiset in every language
                body = re.sub(r"<table>.*?</table>|<[^>]+>|B40623829|\{path:[^}]*\}", " ", text.split('<article class="prose">', 1)[1].split("</article>", 1)[0], flags=re.S)
                return sorted(re.sub(r"[.,\s]", "", n) for n in re.findall(r"\d{1,3}(?:[.,\u00a0\u202f]\d{3})+(?!\d)|\d+(?:[.,]\d+)?", body))
            es_src = build.page_meta((build.SRC / "legal" / f"{page}.html").read_text(encoding="utf-8"))[0]
            expected = {(LOC["locales"][c]["hreflang"], self.site.url(page, c)) for c in ["es", *codes]} | {("x-default", f"/{page}")}
            for c in codes:
                cfg = LOC["locales"][c]
                rel = f"{cfg['dir']}/{page}.html"
                text = self.pages[rel]
                self.assertIn(f'<html lang="{cfg["lang"]}">', text, rel)
                articles = re.findall(r'<div data-v="[^"]+"[^>]*>(.*?)</div>\n(?=<div data-v|</main>)', text, flags=re.S) or [text]
                self.assertEqual(len(articles), 2 if "variant" in cfg else 1, rel)
                for art in articles:
                    self.assertEqual(re.findall(r'<section (?:data-)?id="([^"]+)">\n<h2>', art), es_ids, rel)
                    self.assertEqual(figures(art), figures(es), rel)
                    self.assertEqual(prose(art), prose(es), rel)
                    self.assertNotIn("Vigente desde", art, rel)
                    self.assertNotIn("En esta página", art, rel)
                alts = set(re.findall(r'<link rel="alternate" hreflang="([^"]+)" href="https://www\.sofia\.ismx\.app([^"]+)">', text))
                self.assertEqual(alts, expected, rel)
                self.assertIn(f'<link rel="canonical" href="{HOST}/{cfg["dir"]}/{page}">', text, rel)
                self.assertIn('data-i18n="legal.translated"', text, rel)
                for f in {cfg["file"], cfg.get("variant", {}).get("file", cfg["file"])}:
                    meta = build.page_meta((build.SRC / "legal" / f / f"{page}.html").read_text(encoding="utf-8"))[0]
                    self.assertEqual(meta["version"], es_src["version"], f)
            self.assertEqual(set(re.findall(r'<link rel="alternate" hreflang="([^"]+)" href="https://www\.sofia\.ismx\.app([^"]+)">', es)), expected)
            for c in LOC["order"]:
                index = self.pages[self.site.out_path("index", c)]
                href = f"/{page}" if c == "es" else f"/{LOC['locales'][c if c in codes else build.FALLBACK_LEGAL]['dir']}/{page}"
                self.assertIn(f'data-i18n="foot.{page}" href="{href}"', index, c)
                self.assertNotIn('data-i18n="legal.translated"', index, c)

    # 14e. the /pt/ legal text switches with the rest of the page, and the switch covers its chrome
    def test_translated_legal_portuguese_variant(self):
        for page, codes in build.TRANSLATED_LEGAL.items():
            if "pt" not in codes:
                continue
            text = self.pages[f"pt/{page}.html"]
            self.assertIn('<div data-v="pt-BR">', text)
            self.assertIn('<div data-v="pt-PT" lang="pt-PT" hidden>', text)
            data = json.loads(re.search(r"window\.SOFIA=(.*?);</script>", text).group(1))
            keys = set(re.findall(r'data-i18n(?:-aria|-title)?="([^"]+)"', text))
            self.assertEqual(sorted(keys - set(data["variant"]["dict"])), [])
            js = (self.out / "assets" / "site.js").read_text(encoding="utf-8")
            self.assertIn("querySelectorAll('[data-v]')", js)

    # 14g. no page repeats an id (the hidden Portuguese text keeps its own in data-id)
    def test_no_duplicate_ids(self):
        for rel, text in self.pages.items():
            ids = re.findall(r'<[a-zA-Z][^>]*?\sid="([^"]+)"', text)
            self.assertEqual(sorted({i for i in ids if ids.count(i) > 1}), [], rel)

    # 14f. a note left for whoever publishes (⟨chat⟩, ⟨PENDIENTE…⟩) never reaches the release build
    def test_release_refuses_notes(self):
        with tempfile.TemporaryDirectory() as t:
            f = Path(t) / "x.html"
            f.write_text("<p>a <!--⟨chat⟩-->b</p>", encoding="utf-8")
            draft, release = build.Site(release=False), build.Site(release=True)
            old, build.SRC = build.SRC, Path(t)
            try:
                self.assertIn("⟨chat⟩", draft.source(f))
                with self.assertRaises(build.BuildError):
                    release.source(f)
                f.write_text("<p>a <!-- comentario --> b</p>", encoding="utf-8")
                release.source(f)
            finally:
                build.SRC = old
        for rel, text in self.pages.items():
            self.assertNotIn("⟨", re.sub(r"<!--⟨[^>]*-->", "", text), rel)
        with tempfile.TemporaryDirectory() as t:
            f = Path(t) / "y.json"
            f.write_text('{"k": "a <!-- ver ⟨chat⟩\n con el plan --> b"}', encoding="utf-8")
            old, build.SRC = build.SRC, Path(t)
            try:
                with self.assertRaises(build.BuildError):
                    release.source(f)
            finally:
                build.SRC = old

    # 15. no generated page falls under a universal-link pattern of the AASA
    def test_pages_outside_universal_links(self):
        aasa = json.loads((SRC / "static" / ".well-known" / "apple-app-site-association").read_text(encoding="utf-8"))
        comps = [c["/"] for c in aasa["applinks"]["details"][0]["components"]]
        rx = [re.compile("^" + re.escape(c).replace(r"\*", ".*") + "$") for c in comps]
        urls = {self.site.url(p, c) for p in build.LOCALIZED_PAGES for c in LOC["order"]} | {f"/{p}" for p in build.LEGAL_PAGES}
        for u in urls:
            for r in rx:
                self.assertIsNone(r.match(u), f"{u} would open the app ({r.pattern})")

    # 21. /licenses: every notice of both apps with a working link to its text, and the release refuses a
    #     list built without one of its inputs (the app ships the same lists: apps/mobile/tools/licenses)
    def test_licenses_page(self):
        page = self.pages[f"{build.LICENSES_PAGE}.html"]
        ids = parse(page).ids
        for platform, _ in build.LICENSE_LISTS:
            data = json.loads((SRC / "data" / "licenses" / f"{platform}.json").read_text(encoding="utf-8"))
            self.assertIn(f'<p>{len(data["notices"])} componentes.</p>', page, platform)
            for key in data["texts"]:
                self.assertIn(build.Site.text_anchor(key), ids, f"{platform}: text {key} has no anchor")
            for n in data["notices"]:
                for ref in [n.get("text"), *n.get("also", [])]:
                    if ref:
                        self.assertIn(f'href="#{build.Site.text_anchor(ref)}"', page, n["id"])
        self.assertIn(f'href="/{build.LICENSES_PAGE}"', self.pages["index.html"], "the footer links /licenses")
        aasa = json.loads((SRC / "static" / ".well-known" / "apple-app-site-association").read_text(encoding="utf-8"))
        for c in aasa["applinks"]["details"][0]["components"]:
            self.assertIsNone(re.match("^" + re.escape(c["/"]).replace(r"\*", ".*") + "$", f"/{build.LICENSES_PAGE}"))

    def test_release_refuses_incomplete_licenses(self):
        with tempfile.TemporaryDirectory() as t:
            site = build.Site(release=True)
            original = build.load_json
            def incomplete(path):
                data = original(path)
                return {**data, "incomplete": ["maven"]} if path.name == "android.json" else data
            build.load_json = incomplete
            try:
                with self.assertRaises(build.BuildError):
                    site.build_licenses()
            finally:
                build.load_json = original

    # 16. 404 is served at any depth: nothing relative, no index
    def test_404_works_at_any_depth(self):
        text = self.pages["404.html"]
        self.assertIn('<meta name="robots" content="noindex">', text)
        self.assertNotIn('rel="canonical"', text)
        for tag, attr, url in parse(text).refs:
            self.assertTrue(url.startswith(("/", "#", "mailto:", "https://")), f"relative {tag} {attr}={url}")

    # 13b. same output under another hash seed, in another process
    def test_deterministic_across_processes(self):
        import subprocess
        with tempfile.TemporaryDirectory() as t:
            env = dict(os.environ, PYTHONHASHSEED="12345")
            subprocess.run([sys.executable, str(ROOT / "tools" / "build.py"), "--out", f"{t}/o"], check=True, env=env, capture_output=True)
            a = {p.relative_to(Path(t) / "o"): p.read_bytes() for p in (Path(t) / "o").rglob("*") if p.is_file()}
        b = {p.relative_to(self.out): p.read_bytes() for p in self.out.rglob("*") if p.is_file()}
        self.assertEqual(sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k)), [])

    # 17. the release build refuses pending fields, and a failed build keeps the previous output
    def test_release_refuses_pending_and_keeps_output(self):
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / "docs"
            build.Site(release=False).build(out)
            before = sorted(p.relative_to(out) for p in out.rglob("*"))
            site = build.Site(release=True)
            site.tpl["footer"] += "{{pending:probe}}"
            with self.assertRaises(build.BuildError):
                site.build(out)
            self.assertEqual(sorted(p.relative_to(out) for p in out.rglob("*")), before)
            self.assertEqual([p.name for p in Path(t).iterdir()], ["docs"], "temporary build left behind")

    # 18. merge gate: with SOFIA_RELEASE=1 the committed docs/ must be the release build
    def test_committed_docs_are_release(self):
        if os.environ.get("SOFIA_RELEASE") != "1":
            self.skipTest("SOFIA_RELEASE not set (run before merging to main)")
        with tempfile.TemporaryDirectory() as t:
            build.Site(release=True).build(Path(t) / "rel")
            docs = ROOT / "docs"
            stale = [str(p.relative_to(Path(t) / "rel")) for p in (Path(t) / "rel").rglob("*") if p.is_file()
                     and (docs / p.relative_to(Path(t) / "rel")).read_bytes() != p.read_bytes()]
            self.assertEqual(stale, [])
        for p in (ROOT / "docs").rglob("*.html"):
            self.assertNotIn('class="pending"', p.read_text(encoding="utf-8"), p)


class BrowserTest(unittest.TestCase):
    """Behaviour that only exists in the browser: Portuguese variant, language suggestion, 404 router.
    Served by a tiny server that answers like Pages (extensionless .html, 404.html, folder redirect)."""

    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest("playwright not installed")
        import http.server, threading
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name) / "docs"
        build.Site(release=False).build(out)

        class Pages(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **k): super().__init__(*a, directory=str(out), **k)
            def log_message(self, *a): pass
            def send_head(self):
                path = self.path.split("?")[0]
                fs = out / path.lstrip("/")
                if fs.is_dir() and not path.endswith("/"):
                    self.send_response(301); self.send_header("Location", path + "/"); self.end_headers(); return None
                if not fs.exists() and Path(str(fs) + ".html").exists():
                    self.path = path + ".html"
                elif not fs.exists() or (fs.is_dir() and not (fs / "index.html").exists()):
                    f = open(out / "404.html", "rb"); self.send_response(404)
                    self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); return f
                return super().send_head()

        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Pages)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.pw.stop(); cls.server.shutdown(); cls.tmp.cleanup()

    def page(self, url, locale="es-ES", ua=None):
        ctx = self.browser.new_context(locale=locale, user_agent=ua) if ua else self.browser.new_context(locale=locale)
        p = ctx.new_page(); errors = []
        p.on("pageerror", lambda e: errors.append(str(e)))
        p.goto(self.base + url); p.wait_for_timeout(250)
        self.addCleanup(ctx.close)
        return p, errors

    def test_portuguese_variant(self):
        p, err = self.page("/pt/", "pt-PT")
        self.assertEqual(p.evaluate("document.documentElement.lang"), "pt-PT")
        self.assertIn("A tua história", p.inner_text("h1"))
        self.assertIn("A tua história", p.title())
        p2, _ = self.page("/pt/?v=br", "pt-PT")
        self.assertEqual(p2.evaluate("document.documentElement.lang"), "pt-BR")
        self.assertTrue(p2.is_visible("body"))
        self.assertEqual(err, [])

    def test_language_suggestion_only_for_other_languages(self):
        p, _ = self.page("/faq", "en-US")
        self.assertTrue(p.is_visible("#suggest"))
        self.assertEqual(p.get_attribute("#suggest a", "href"), "/en/faq")
        p.click("#suggest .x")
        p2 = p.context.new_page(); p2.goto(self.base + "/faq"); p2.wait_for_timeout(200)
        self.assertFalse(p2.is_visible("#suggest"), "dismissal is remembered")
        p3, _ = self.page("/", "es-ES")
        self.assertFalse(p3.is_visible("#suggest"))

    def test_router(self):
        android = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Mobile Safari/537.36"
        # A shared link carries only its code (`/entry?code=`): no id in the URL.
        p, err = self.page("/entry?code=K7Q2M9X", "de-DE", android)
        self.assertTrue(p.is_visible("#shared")); self.assertFalse(p.is_visible("#notfound"))
        self.assertEqual(p.get_attribute("#open-app", "href"), "intent://entry?code=K7Q2M9X#Intent;scheme=sofia;package=com.ismx.sofia;end")
        self.assertEqual(p.evaluate("document.documentElement.lang"), "de")
        p2, _ = self.page("/user?code=K7Q2M9X", "es-ES", android)
        self.assertIn("diario", p2.inner_text("#shared-title")); self.assertFalse(p2.is_visible("#shared-preview"))
        p3, _ = self.page("/entry/abc", "es-ES", android)
        self.assertTrue(p3.is_visible("#shared"))
        for url in ("/entry/quick", "/entry/inbox/x", "/entry", "/nada/que/ver"):
            q, _ = self.page(url)
            self.assertTrue(q.is_visible("#notfound"), url); self.assertFalse(q.is_visible("#shared"), url)
        self.assertEqual(err, [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
