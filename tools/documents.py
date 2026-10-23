"""The legal documents' history: /documents, one page per version, the frozen originals and the
manifest that core ingests (plan 02-running/plans/261022-documentos-legales.md).

The registry is src/data/documents.json. A version's frozen file is the exact page that was in force:
its bytes never change and its sha256 is the proof of what a person was shown. Everything that
describes a version (its date, whether it asks for acceptance, what changes) lives outside those
bytes, in the version's own page, which frames the original instead of rewriting it.

    /documents/                          every document and its timeline
    /documents/<doc>/                    opens the version in force, or the announced one if there is one
    /documents/<doc>/<YYMMDD>            one version: date, label, what changes, and the original framed
    /documents/<doc>/original/<YYMMDD>   the frozen bytes (also under /<lang>/ when translated)
    /documents.json (+ .sig)             the manifest, signed with Ed25519 by the release build

What is in force depends on today's date, and the site is static and built deterministically, so the
pages carry the dates and site.js decides the state in the browser; without JavaScript they still say
since when each version applies.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")
MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
          "octubre", "noviembre", "diciembre"]
# The Ed25519 key that signs the manifest, encrypted with SOPS for Israel's age key only (.sops.yaml).
SIGNING_KEY = "secrets/documents-signing.sops.pem"
# Tests sign with a throwaway plaintext key instead of decrypting the real one.
SIGNING_KEY_ENV = "SOFIA_DOCUMENTS_TEST_KEY"
LABELS = {True: "Requiere aceptación", False: "Cambio menor"}
# The icon of each document in the index (symbols of templates/icons.html).
ICONS = {"terms": "i-book", "privacy": "i-lock", "ia": "i-sparkles", "journal": "i-pencil",
         "health": "i-heart", "subscription": "i-star", "normas": "i-users", "cookies": "i-world",
         "legal": "i-flag", "guardian": "i-heart-users", "crisis": "i-bell"}


class DocumentsError(Exception):
    pass


def madrid(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(MADRID)


def long_date(ts: str) -> str:
    d = madrid(ts)
    return f"{d.day} de {MONTHS[d.month - 1]} de {d.year}"


def short_date(ts: str) -> str:
    d = madrid(ts)
    return f"{d.day} {MONTHS[d.month - 1][:3]}{'.' if d.month != 5 else ''} {d.year}"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Documents:
    """Built by composition from the Site: it borrows the chrome, the head and the page frame."""

    def __init__(self, site, *, src: Path, root: Path, release: bool, host: str, page_meta):
        self.site, self.src, self.root, self.release = site, src, root, release
        self.host, self.page_meta = host, page_meta
        self.store = src / "documents"
        self.docs = json.loads((src / "data" / "documents.json").read_text(encoding="utf-8"))["documents"]
        # «What changes» is written per i18n file: the app picks the one of its language.
        self.change_locales = site.change_locales()
        self.web_dirs = {c: site.locales[c]["dir"] for c in site.order}
        self.check()

    # ---------- the registry's contract ----------
    def check(self) -> None:
        seen_ids, seen_web = set(), set()
        for doc in self.docs:
            if doc["id"] in seen_ids or doc["web"] in seen_web:
                raise DocumentsError(f"document {doc['id']!r} registered twice")
            seen_ids.add(doc["id"]); seen_web.add(doc["web"])
            if not doc["versions"]:
                raise DocumentsError(f"{doc['id']}: no versions")
            previous = None
            for i, v in enumerate(doc["versions"]):
                where = f"{doc['id']} {v['version']}"
                if int(madrid(v["effective"]).strftime("%y%m%d")) != v["version"]:
                    raise DocumentsError(f"{where}: the version is not the Madrid date of its effective time")
                if previous is not None and v["version"] <= previous:
                    raise DocumentsError(f"{where}: versions must grow, one per document and day")
                previous = v["version"]
                if "es" not in v["files"]:
                    raise DocumentsError(f"{where}: no Spanish original")
                for code, f in v["files"].items():
                    if code not in self.web_dirs:
                        raise DocumentsError(f"{where}: unknown web language {code!r}")
                    path = self.store / f["path"]
                    if not path.is_file():
                        raise DocumentsError(f"{where}: missing src/documents/{f['path']}")
                    if sha256(path) != f["sha256"]:
                        raise DocumentsError(f"{where}: src/documents/{f['path']} changed; a frozen version never does")
                unknown = set(v["changes"]) - set(self.change_locales)
                if unknown:
                    raise DocumentsError(f"{where}: «what changes» in unknown languages {sorted(unknown)}")
                if i == 0 and v["changes"]:
                    raise DocumentsError(f"{where}: a first version has no «what changes»")
                if i > 0:
                    missing = [c for c in self.change_locales if not v["changes"].get(c)]
                    if missing:
                        if self.release:
                            raise DocumentsError(f"{where}: «what changes» missing in {', '.join(missing)}")
                        self.site.pending.append(f"qué cambia de {doc['id']} {v['version']} en {', '.join(missing)}")

    # ---------- urls ----------
    @staticmethod
    def page_url(doc, v) -> str:
        return f"/documents/{doc['web']}/{v['version']}"

    def original_url(self, doc, v, code="es") -> str:
        d = self.web_dirs[code]
        return (f"/{d}" if d else "") + f"/documents/{doc['web']}/original/{v['version']}"

    # ---------- shared pieces ----------
    def browser_data(self) -> list:
        """What site.js needs to tell the version in force from the announced and the old ones."""
        return [{"doc": d["web"], "versions": [{"v": v["version"], "at": int(madrid(v["effective"]).timestamp() * 1000),
                                                 "url": self.page_url(d, v)} for v in d["versions"]]} for d in self.docs]

    def description(self, doc) -> str:
        path = self.src / "legal" / f"{doc['web']}.html"
        if path.exists():
            meta, _ = self.page_meta(path.read_text(encoding="utf-8"))
            return meta.get("description", doc["title"])
        return doc["title"]

    @staticmethod
    def state_chips(doc, v) -> str:
        """Filled in by site.js from today's date; without JavaScript they stay hidden."""
        return (f'<span class="chip" data-when="current" hidden>Vigente</span>'
                f'<span class="chip next" data-when="next" hidden>Próxima</span>'
                f'<span class="chip old" data-when="old" hidden>Anterior</span>')

    @staticmethod
    def label(i, v) -> str:
        if i == 0:
            return ""
        cls = "ask" if v["material"] else "minor"
        return f'<span class="chip {cls}">{LABELS[v["material"]]}</span>'

    def page(self, *, path_key: str, title: str, description: str, canonical: str, main: str, data: dict) -> str:
        s = self.site
        header = s.chrome("header", path_key, "es", "es", "es_es")
        footer = s.chrome("footer", path_key, "es", "es", "es_es")
        head = s.head(code="es", title=f"{title} · SofIA", description=description, canonical=canonical, alternates=[])
        data = {"words": {"carousel": "carrusel", "card": "Ir a la tarjeta"}, **data}
        return s.document(code="es", head=head, body=f"{header}\n<main>\n{main}\n</main>\n{footer}", data=data)

    # ---------- /documents/ ----------
    def build_index(self) -> str:
        e = html.escape
        items = []
        for doc in self.docs:
            rows = []
            for i, v in reversed(list(enumerate(doc["versions"]))):
                changes = v["changes"].get("es")
                rows.append(
                    f'<li data-doc="{doc["web"]}" data-version="{v["version"]}"><span class="dot" aria-hidden="true"></span>'
                    f'<div class="row"><strong>{long_date(v["effective"])}</strong>{self.state_chips(doc, v)}{self.label(i, v)}</div>'
                    + (f"<p>{e(changes)}</p>" if changes else "")
                    + f'<a href="{self.page_url(doc, v)}">Leer esta versión</a></li>')
            latest = doc["versions"][-1]
            icon = ICONS.get(doc["id"], "i-text")
            items.append(
                f'<details class="doc" id="{doc["web"]}">\n<summary>'
                f'<span class="ic" aria-hidden="true"><svg class="ico"><use href="#{icon}"></use></svg></span>'
                f'<span class="dt"><b>{e(doc["title"])}</b><small>{e(self.description(doc))}</small></span>'
                f'<span class="st"><span class="chip" data-doc-chip="current" data-doc="{doc["web"]}" hidden></span>'
                f'<span class="chip next" data-doc-chip="next" data-doc="{doc["web"]}" hidden></span></span>'
                f'<span class="plus" aria-hidden="true"></span></summary>\n'
                f'<div class="body">\n<p class="doc-cta"><a href="/documents/{doc["web"]}/"><b>Leer la versión vigente</b></a>'
                f'<span class="muted">Última versión publicada: {long_date(latest["effective"])}</span></p>\n'
                f'<ol class="tl">\n' + "\n".join(rows) + "\n</ol>\n</div>\n</details>")
        main = ('<section class="page-hero" id="documents">\n<div aria-hidden="true" class="mesh"><i></i><i></i><i></i></div>\n'
                '<div class="wrap">\n<span class="eyebrow">Legal</span>\n<h1><span class="grad-text">Documentos legales</span></h1>\n'
                '<p class="lede">Todo lo que rige SofIA, con cada versión que ha tenido: qué cambió y desde cuándo vale. '
                'Abre un documento para ver su historial.</p>\n</div>\n</section>\n'
                f'<div class="wrap docs">\n' + "\n".join(items) + "\n</div>\n")
        return self.page(path_key="documents", title="Documentos legales",
                         description="Los documentos legales de SofIA y todas sus versiones: qué cambió y desde cuándo vale.",
                         canonical="/documents/", main=main, data={"documents": self.browser_data()})

    # ---------- /documents/<doc>/ ----------
    def build_open(self, doc) -> str:
        """Opens the version in force or the announced one (site.js). Without JavaScript: the list."""
        links = "".join(f'<li><a href="{self.page_url(doc, v)}">{long_date(v["effective"])}</a></li>'
                        for v in reversed(doc["versions"]))
        main = (f'<section class="page-hero" data-doc-open="{doc["web"]}">\n<div class="wrap">\n'
                f'<nav class="crumbs" aria-label="Ruta"><a href="/documents/">Documentos legales</a></nav>\n'
                f'<h1><span class="grad-text">{html.escape(doc["title"])}</span></h1>\n'
                f'<p class="lede">Sus versiones:</p>\n<ul class="doc-list">{links}</ul>\n</div>\n</section>\n')
        return self.page(path_key="documents", title=doc["title"], description=self.description(doc),
                         canonical=f"/documents/{doc['web']}/", main=main, data={"documents": self.browser_data()})

    # ---------- /documents/<doc>/<YYMMDD> ----------
    def build_version(self, doc, i, v) -> str:
        e = html.escape
        versions = doc["versions"]
        nxt = versions[i + 1] if i + 1 < len(versions) else None
        changes = v["changes"].get("es")
        current = ' aria-current="page"'
        others = "".join(
            f'<li><a href="{self.page_url(doc, o)}"{current if o is v else ""}>'
            f'<span>{short_date(o["effective"])}</span><small data-doc="{doc["web"]}" data-version="{o["version"]}">'
            f'{self.state_chips(doc, o)}</small></a></li>'
            for o in reversed(versions))
        langs = [c for c in v["files"] if c != "es"]
        translated = ("" if not langs else
                      '<p class="orig-langs">Este texto en: ' + " · ".join(
                          f'<a href="{self.original_url(doc, v, c)}" hreflang="{self.site.locales[c]["hreflang"]}" '
                          f'lang="{self.site.locales[c]["lang"]}">{self.site.locales[c]["name"]}</a>' for c in langs) + "</p>\n")
        notes = (f'<div class="doc-note next" data-when="next" hidden><p><b>Esta versión todavía no está en vigor.</b> '
                 f'Empieza a aplicarse el {long_date(v["effective"])}.</p>'
                 f'<a href="/documents/{doc["web"]}/">Leer la versión vigente</a></div>\n'
                 f'<div class="doc-note old" data-when="old" hidden><p><b>Esta versión ya no está en vigor.</b>'
                 + (f' La sustituyó la del {long_date(nxt["effective"])}.' if nxt else "")
                 + '</p><a href="/documents/' + doc["web"] + '/">Leer la versión vigente</a></div>\n')
        change = ("" if i == 0 else
                  f'<section class="change" aria-label="Qué cambia">\n<h2>Qué cambia en esta versión</h2>\n'
                  f'<p>{e(changes) if changes else "{{pending:qué cambia en esta versión}}"}</p>\n</section>\n')
        main = (f'<section class="page-hero doc-version" data-doc="{doc["web"]}" data-version="{v["version"]}">\n'
                '<div aria-hidden="true" class="mesh"><i></i><i></i><i></i></div>\n<div class="wrap">\n'
                f'<nav class="crumbs" aria-label="Ruta"><a href="/documents/">Documentos legales</a><span aria-hidden="true">›</span>'
                f'<a href="/documents/{doc["web"]}/">{e(doc["title"])}</a></nav>\n'
                f'{notes}<span class="eyebrow">Legal</span>\n<h1><span class="grad-text">{e(doc["title"])}</span></h1>\n'
                f'<div class="meta-pills">{self.state_chips(doc, v)}<span>Desde el <b>{long_date(v["effective"])}</b></span>'
                + (f'<span>{LABELS[v["material"]]}</span>' if i > 0 else "")
                + f'<details class="ver"><summary>Otras versiones</summary><ul>{others}</ul></details></div>\n'
                f'{change}</div>\n</section>\n'
                f'<section class="wrap orig" aria-label="Texto original de esta versión">\n'
                f'<div class="orig-bar"><span><b>Texto original de esta versión</b> · tal como se publicó, sin cambios</span>'
                f'<a href="{self.original_url(doc, v)}">Abrir aparte</a></div>\n{translated}'
                f'<iframe class="orig-frame" src="{self.original_url(doc, v)}" title="{e(doc["title"])}, texto original" loading="lazy"></iframe>\n'
                '</section>\n')
        main = self.site.fill(main, "es", "es", "es_es")
        return self.page(path_key="documents", title=f"{doc['title']} · {long_date(v['effective'])}",
                         description=self.description(doc), canonical=self.page_url(doc, v), main=main,
                         data={"documents": self.browser_data()})

    # ---------- the manifest ----------
    def manifest(self) -> str:
        """What core ingests. Deterministic: the same registry gives the same bytes, so the signature
        is reproducible. `locales` are i18n files; the pt page carries both Portuguese texts."""
        by_file = {}
        for c in self.site.order:
            cfg = self.site.locales[c]
            by_file[cfg["file"]] = c
            if "variant" in cfg:
                by_file[cfg["variant"]["file"]] = c
        out = []
        for doc in self.docs:
            versions = []
            for v in doc["versions"]:
                locales = {}
                for loc in self.change_locales:
                    web = by_file[loc]
                    f = v["files"].get(web)
                    locales[loc] = {"url": self.original_url(doc, v, web) if f else None,
                                    "sha256": f["sha256"] if f else None,
                                    "changes": v["changes"].get(loc)}
                versions.append({"version": v["version"], "effective": madrid(v["effective"]).isoformat(),
                                 "material": v["material"], "url": self.page_url(doc, v), "locales": locales})
            out.append({"id": doc["id"], "versions": versions})
        return json.dumps({"origin": self.host, "documents": out}, ensure_ascii=False, separators=(",", ":")) + "\n"

    def sign(self, manifest: Path) -> str:
        """Ed25519 over the manifest's bytes, base64. The key is decrypted with sops into a pipe and
        never touches the disk."""
        test_key = os.environ.get(SIGNING_KEY_ENV)
        if test_key:
            key = Path(test_key).read_bytes()
        else:
            sops = shutil.which("sops") or os.environ.get("SOPS")
            if not sops:
                raise DocumentsError("signing the manifest needs sops (and Israel's age key)")
            run = subprocess.run([sops, "--decrypt", "--input-type", "binary", "--output-type", "binary",
                                  str(self.root / SIGNING_KEY)], capture_output=True)
            if run.returncode != 0:
                raise DocumentsError("could not decrypt the signing key: " + run.stderr.decode(errors="replace").strip())
            key = run.stdout
        r, w = os.pipe()
        try:
            os.write(w, key)
            os.close(w)
            run = subprocess.run(["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", f"/dev/fd/{r}",
                                  "-in", str(manifest)], capture_output=True, pass_fds=(r,))
        finally:
            os.close(r)
        if run.returncode != 0 or len(run.stdout) != 64:
            raise DocumentsError("signing the manifest failed: " + run.stderr.decode(errors="replace").strip())
        return base64.b64encode(run.stdout).decode() + "\n"

    # ---------- everything ----------
    def files(self) -> dict[str, str]:
        out = {"documents/index.html": self.build_index()}
        for doc in self.docs:
            out[f"documents/{doc['web']}/index.html"] = self.build_open(doc)
            for i, v in enumerate(doc["versions"]):
                out[f"documents/{doc['web']}/{v['version']}.html"] = self.build_version(doc, i, v)
        return out

    def copies(self) -> dict[str, Path]:
        """Byte-for-byte: each original at its /documents URL and, for the versions published before
        /documents, at the old URL the app and the database still point to."""
        out = {}
        for doc in self.docs:
            for v in doc["versions"]:
                for code, f in v["files"].items():
                    src = self.store / f["path"]
                    out[self.original_url(doc, v, code).lstrip("/") + ".html"] = src
                    if f.get("legacy"):
                        out[f["legacy"]] = src
        return out

    def sitemap_urls(self) -> list[str]:
        urls = ["/documents/"]
        for doc in self.docs:
            urls.append(f"/documents/{doc['web']}/")
            urls += [self.page_url(doc, v) for v in doc["versions"]]
        return urls
