#!/usr/bin/env python3
"""Build the SofIA website into docs/ (what GitHub Pages serves).

Pages are prerendered once per published language from src/pages/*.html and src/i18n/*.json.
Legal documents live in src/legal/ and are published in Spanish only, at the root.

    python3 tools/build.py            # draft: pending fields render highlighted
    python3 tools/build.py --release  # fails if anything is pending or a legal document is missing
    python3 tools/build.py --out DIR  # build somewhere else (used by the tests)

Standard library only, deterministic output (no timestamps), Python 3.11+.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import tempfile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
HOST = "https://www.sofia.ismx.app"
APP_STORE_ID = "6757621393"
ANDROID_PACKAGE = "com.ismx.sofia"

LOCALIZED_PAGES = ["index", "faq", "help", "delete-account"]
# The privacy summary in the visitor's language (GDPR art. 12). Spanish has the full policy instead, so
# its Spanish URL is the legal document and it is built for every other language only.
PRIVACY_BASIC = "privacy-basic"
# Legal documents the app or the stores link to. The release build refuses to run without all of them.
LEGAL_PAGES = {
    "legal": "Aviso legal",
    "terms": "Términos y condiciones",
    "privacy": "Política de privacidad",
    "ia": "Política de IA",
    "normas": "Normas de la comunidad",
    "cookies": "Política de cookies",
    "sensitive": "Consentimientos de datos sensibles",
}

TOKEN = re.compile(r"\{\{(t|a|steps|url|pending):([^}]*)\}\}")
INLINE = re.compile(r"\{(path|label):([^}]+)\}")
HEADER = re.compile(r"^<!--\s*(.*?)\s*-->\s*", re.S)


class BuildError(Exception):
    pass


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def page_meta(text: str) -> tuple[dict, str]:
    """Pages start with `<!-- page: x · title-key: k · description-key: k -->`."""
    m = HEADER.match(text)
    if not m:
        raise BuildError("page without header comment")
    meta = {}
    for part in m.group(1).split("·"):
        key, _, value = part.partition(":")
        meta[key.strip()] = value.strip()
    return meta, text[m.end():]


class Site:
    def __init__(self, release: bool):
        self.release = release
        loc = load_json(SRC / "data" / "locales.json")
        self.order = loc["order"]
        self.locales = loc["locales"]
        self.app = load_json(SRC / "data" / "app-labels.json")["labels"]
        self.routes = load_json(SRC / "data" / "routes.json")
        self.dicts = {}
        for code, cfg in self.locales.items():
            self.dicts[cfg["file"]] = load_json(SRC / "i18n" / f"{cfg['file']}.json")
            if "variant" in cfg:
                self.dicts[cfg["variant"]["file"]] = load_json(SRC / "i18n" / f"{cfg['variant']['file']}.json")
        self.tpl = {n: (SRC / "templates" / f"{n}.html").read_text(encoding="utf-8")
                    for n in ("header", "footer", "icons", "mark")}
        self.pending: list[str] = []

    # ---------- urls ----------
    def url(self, page: str, code: str) -> str:
        if page in LEGAL_PAGES or (page == PRIVACY_BASIC and code == "es"):
            return "/privacy" if page == PRIVACY_BASIC else f"/{page}"
        if page == PRIVACY_BASIC:
            return f"/{self.locales[code]['dir']}/privacy"
        prefix = "/" + self.locales[code]["dir"] if self.locales[code]["dir"] else ""
        return (prefix + "/") if page == "index" else f"{prefix}/{page}"

    def out_path(self, page: str, code: str) -> str:
        d = self.locales[code]["dir"]
        if page == PRIVACY_BASIC:
            page = "privacy"
        return (f"{d}/" if d else "") + f"{page}.html"

    # ---------- strings ----------
    def app_path(self, ids: list[str], col: str) -> tuple[str, list[str]]:
        """The labels the app shows. A path is shown in one language only: the locale's if every
        label exists in it, otherwise Spanish (today the app is Spanish)."""
        for i in ids:
            if i not in self.app:
                raise BuildError(f"unknown app label {i!r}: add it to src/data/app-labels.json")
        use = col if all(col in self.app[i] for i in ids) else "es_es"
        lang = "es" if use == "es_es" else None
        return lang, [html.escape(self.app[i][use]) for i in ids]

    def inline(self, value: str, code: str, col: str) -> str:
        def sub(m):
            kind, arg = m.group(1), m.group(2)
            ids = arg.split(",") if kind == "path" else [arg]
            lang, labels = self.app_path(ids, col)
            la = f' lang="{lang}"' if lang and lang != self.locales[code]["lang"][:2] else ""
            if kind == "path":
                return f'<span class="app-label"{la}>{" › ".join(labels)}</span>'
            return f"<b{la}>{labels[0]}</b>"
        value = INLINE.sub(sub, value)
        return re.sub(r"\{\{url:([^}]+)\}\}", lambda m: self.url(m.group(1), code), value)

    def text(self, dict_name: str, key: str, code: str, col: str) -> str:
        d = self.dicts[dict_name]
        if key not in d:
            raise BuildError(f"missing key {key!r} in {dict_name}")
        return self.inline(d[key], code, col)

    def fill(self, tpl: str, code: str, dict_name: str, col: str) -> str:
        def sub(m):
            kind, arg = m.group(1), m.group(2)
            if kind == "t":
                return self.text(dict_name, arg, code, col)
            if kind == "a":
                return html.escape(self.plain(self.text(dict_name, arg, code, col)), quote=True)
            if kind == "steps":
                lang, labels = self.app_path(arg.split(","), col)
                la = f' lang="{lang}"' if lang and lang != self.locales[code]["lang"][:2] else ""
                return "".join(f"<li{la}>{x}</li>" for x in labels)
            if kind == "url":
                return self.url(arg, code)
            if kind == "pending":
                self.pending.append(arg)
                if self.release:
                    raise BuildError(f"pending field {arg!r}")
                return f'<span class="pending">{html.escape(arg)}</span>'
            raise BuildError(kind)
        return TOKEN.sub(sub, tpl)

    # ---------- chrome ----------
    def lang_menu(self, page: str, code: str, footer: bool) -> str:
        items = []
        target = page if page in LOCALIZED_PAGES or page == PRIVACY_BASIC else "index"
        for c in self.order:
            cfg = self.locales[c]
            href = self.url(target, c)
            cur = ' aria-current="page"' if c == code else ""
            if footer:
                items.append(f'<a href="{href}" hreflang="{cfg["hreflang"]}" lang="{cfg["lang"]}"{cur}>{cfg["name"]}</a>')
                continue
            if "variant" in cfg:
                v = cfg["variant"]
                items.append(f'<a href="{href}?v=br" hreflang="{cfg["hreflang"]}" lang="{cfg["lang"]}"{cur}>'
                             f'<span>{cfg["name"]} · {v["baseRegion"]}</span><small>{c}</small></a>')
                items.append(f'<a href="{href}?v=pt" hreflang="{cfg["hreflang"]}" lang="{v["lang"]}">'
                             f'<span>{cfg["name"]} · {v["region"]}</span><small>{c}</small></a>')
            else:
                items.append(f'<a href="{href}" hreflang="{cfg["hreflang"]}" lang="{cfg["lang"]}"{cur}>'
                             f'<span>{cfg["name"]}</span><small>{c}</small></a>')
        return "".join(items)

    def chrome_tpl(self, part: str, page: str, code: str) -> str:
        """Header or footer with the language menus and notes expanded, still with {{t:}} tokens."""
        t = self.tpl[part]
        t = t.replace("{{langcode}}", code.upper()).replace("{{langpanel}}", self.lang_menu(page, code, False))
        t = t.replace("{{langlinks}}", self.lang_menu(page, code, True))
        note = ""
        if code != "es":
            note = ('<p class="foot-note"><svg class="ico" aria-hidden="true"><use href="#i-world"></use></svg>'
                    f'<span data-i18n="legal.langnote">{{{{t:legal.langnote}}}}</span></p>')
        privacy = self.url(PRIVACY_BASIC, code)
        t = t.replace("{{privacylink}}", f'href="{privacy}"' + (' hreflang="es"' if code == "es" else ""))
        return t.replace("{{legalnote}}", note)

    def chrome(self, part: str, page: str, code: str, dict_name: str, col: str) -> str:
        return self.fill(self.chrome_tpl(part, page, code), code, dict_name, col)

    def head(self, *, code, title, description, canonical, alternates, noindex=False, extra="", banner=True):
        cfg = self.locales[code]
        h = [
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">',
            f"<title>{html.escape(title)}</title>",
            f'<meta name="description" content="{html.escape(description, quote=True)}">',
            '<meta name="theme-color" content="#3B7BFF">',
            '<meta name="color-scheme" content="light dark">',
            '<link rel="icon" href="/assets/img/icon.svg" type="image/svg+xml">',
            '<link rel="icon" href="/assets/img/favicon-32.png" sizes="32x32" type="image/png">',
            '<link rel="apple-touch-icon" href="/assets/img/apple-touch-icon.png">',
            '<link rel="preload" href="/assets/fonts/rem-700.woff2" as="font" type="font/woff2" crossorigin>',
            '<link rel="stylesheet" href="/assets/site.css">',
        ]
        if banner:
            h.insert(6, f'<meta name="apple-itunes-app" content="app-id={APP_STORE_ID}">')
        if noindex:
            h.append('<meta name="robots" content="noindex">')
        else:
            h.append(f'<link rel="canonical" href="{HOST}{canonical}">')
            for hl, href in alternates:
                h.append(f'<link rel="alternate" hreflang="{hl}" href="{HOST}{href}">')
            h += [
                '<meta property="og:type" content="website">',
                '<meta property="og:site_name" content="SofIA">',
                f'<meta property="og:title" content="{html.escape(title, quote=True)}">',
                f'<meta property="og:description" content="{html.escape(description, quote=True)}">',
                f'<meta property="og:url" content="{HOST}{canonical}">',
                f'<meta property="og:image" content="{HOST}/assets/img/og.jpg">',
                '<meta property="og:image:width" content="1200">',
                '<meta property="og:image:height" content="630">',
                f'<meta property="og:locale" content="{cfg["og"]}">',
                '<meta name="twitter:card" content="summary_large_image">',
            ]
        return "\n".join(h) + extra

    def document(self, *, code, head, body, data) -> str:
        cfg = self.locales[code]
        payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).replace("<", "\\u003c")
        script = "<script>window.SOFIA=" + payload + ";</script>"
        return (f'<!doctype html>\n<html lang="{cfg["lang"]}">\n<head>\n{head}\n</head>\n<body>\n'
                f'{self.tpl["mark"]}\n{self.tpl["icons"]}\n{body}\n{script}\n'
                '<script src="/assets/site.js" defer></script>\n</body>\n</html>\n')

    # ---------- page data ----------
    def aud(self, dict_name, code, col):
        return [{k: self.text(dict_name, f"aud.{n}.{k}", code, col) for k in ("chip", "who", "text")} for n in range(4)]

    def keys_in(self, tpl: str) -> list[str]:
        return sorted(set(re.findall(r"\{\{[ta]:([^}]+)\}\}", tpl)))

    def strip_title(self, s: str) -> str:
        return self.plain(s)

    @staticmethod
    def plain(s: str) -> str:
        """Text without markup or entities; callers escape it for the attribute or element."""
        return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()

    def suggest_map(self, page):
        out = {}
        for c in self.order:
            if c == "es":
                continue
            cfg, d = self.locales[c], self.dicts[self.locales[c]["file"]]
            out[c] = {"text": d["suggest.text"], "go": d["suggest.go"], "close": d["suggest.close"],
                      "href": self.url(page, c), "lang": cfg["lang"]}
        return out

    # ---------- builders ----------
    def build_localized(self, page: str, code: str) -> str:
        cfg = self.locales[code]
        meta, body_tpl = page_meta((SRC / "pages" / f"{page}.html").read_text(encoding="utf-8"))
        name, col = cfg["file"], cfg["col"]
        main = self.fill(body_tpl, code, name, col)
        title = self.strip_title(self.text(name, meta["title-key"], code, col))
        if page != "index":
            title = f"{title} · SofIA"
        description = self.strip_title(self.text(name, meta["description-key"], code, col))
        alternates = [(self.locales[c]["hreflang"], self.url(page, c)) for c in self.order] + [("x-default", self.url(page, "es"))]
        extra = ""
        data = {"aud": self.aud(name, code, col), "words": {"carousel": cfg["carousel"], "card": cfg["card"]}}
        if "variant" in cfg:
            v = cfg["variant"]
            keys = self.keys_in(self.chrome_tpl("header", page, code) + self.chrome_tpl("footer", page, code) + body_tpl)
            data["variant"] = {"lang": v["lang"],
                               "dict": {k: self.text(v["file"], k, code, v["col"]) for k in keys},
                               "aud": self.aud(v["file"], code, v["col"])}
            vt = self.strip_title(self.text(v["file"], meta["title-key"], code, v["col"]))
            data["variant"]["dict"]["__title"] = vt if page == "index" else f"{vt} · SofIA"
            # Decide the variant before the first paint so nobody sees the page switch language.
            extra = ("\n<script>(function(){var h=document.documentElement,q=new URLSearchParams(location.search).get('v'),s=null;"
                     "try{if(q){localStorage.setItem('sofia-pt',q)}s=localStorage.getItem('sofia-pt')}catch(e){}"
                     "var n=(navigator.languages&&navigator.languages[0])||navigator.language||'';"
                     f"if(s==='pt'||(!s&&/^pt-pt/i.test(n))){{h.lang='{v['lang']}';h.classList.add('pt-swap')}}}})();</script>")
        if code == "es":
            data["suggest"] = self.suggest_map(page)
        header = self.chrome("header", page, code, name, col)
        footer = self.chrome("footer", page, code, name, col)
        title_attr = '<span hidden data-i18n-title="__title"></span>' if "variant" in cfg else ""
        suggest = ('<aside class="suggest" id="suggest" hidden><p></p><a class="btn sm" href="/"></a>'
                   '<button class="x" type="button">×</button></aside>') if code == "es" else ""
        body = f"{header}\n<main>\n{main}\n</main>\n{footer}\n{suggest}{title_attr}"
        head = self.head(code=code, title=title, description=description, canonical=self.url(page, code),
                         alternates=alternates, extra=extra)
        return self.document(code=code, head=head, body=body, data=data)

    @staticmethod
    def label_cells(m) -> str:
        """Each cell carries its column's heading, so a narrow screen can show the rows as cards."""
        table = m.group(0)
        heads = [re.sub(r"<[^>]+>", "", h) for h in re.findall(r"<th>(.*?)</th>", table, flags=re.S)]
        def row(r):
            cells = iter(heads)
            return re.sub(r"<td>", lambda _: f'<td data-label="{html.escape(next(cells, ""), quote=True)}">', r.group(0))
        return re.sub(r"<tr>.*?</tr>", row, table, flags=re.S)

    def legal_wrap(self, page: str, meta: dict, body: str) -> str:
        """Legal sources written as `<section id=".." data-title="..">` blocks get the shared frame:
        hero with version and effective date, numbered headings and the table of contents."""
        sections = re.findall(r'<section id="([^"]+)" data-title="([^"]+)">', body)
        n = 0
        def head(m):
            nonlocal n
            n += 1
            return (f'<section id="{m.group(1)}">\n<h2><span class="n grad-text">{n}</span>{m.group(2)}'
                    f'<a class="anchor" href="#{m.group(1)}" aria-label="Enlace a esta sección">#</a></h2>')
        body = re.sub(r'<section id="([^"]+)" data-title="([^"]+)">', head, body)
        body = re.sub(r"<table>.*?</table>", self.label_cells, body, flags=re.S)
        toc = "".join(f'<li><a href="#{i}">{t}</a></li>' for i, t in sections)
        return (f'<section class="page-hero" id="{page}">\n<div aria-hidden="true" class="mesh"><i></i><i></i><i></i></div>\n'
                f'<div class="wrap">\n<span class="eyebrow">Legal</span>\n<h1><span class="grad-text">{meta["title"]}</span></h1>\n'
                f'<p class="lede">{meta["lede"]}</p>\n<div class="meta-pills"><span>Versión {meta["version"]}</span>'
                f'<span>Vigente desde el <b>{{{{pending:fecha de publicación}}}}</b></span></div>\n</div>\n</section>\n'
                f'<div class="wrap doc-layout">\n<nav aria-label="En esta página" class="toc">\n<h2>En esta página</h2>\n<ol>{toc}</ol>\n</nav>\n'
                f'<article class="prose">\n{body.strip()}\n</article>\n</div>\n')

    def build_legal(self, page: str) -> str:
        path = SRC / "legal" / f"{page}.html"
        if path.exists():
            meta, body_tpl = page_meta(path.read_text(encoding="utf-8"))
            title, description = meta.get("title", LEGAL_PAGES[page]), meta.get("description", LEGAL_PAGES[page])
            if "version" in meta:
                body_tpl = self.legal_wrap(page, meta, body_tpl)
        else:
            if self.release:
                raise BuildError(f"legal document {page!r} is missing: write src/legal/{page}.html")
            title, description = LEGAL_PAGES[page], LEGAL_PAGES[page]
            body_tpl = ('<section class="page-hero"><div class="wrap"><span class="eyebrow">Legal</span>'
                        f'<h1><span class="grad-text">{title}</span></h1>'
                        f'<p class="lede">{{{{pending:texto de «{title}» por escribir}}}}</p></div></section>')
        main = self.inline(self.fill(body_tpl, "es", "es", "es_es"), "es", "es_es")
        header = self.chrome("header", page, "es", "es", "es_es")
        footer = self.chrome("footer", page, "es", "es", "es_es")
        alternates = []
        if page == "privacy":
            alternates = [(self.locales[c]["hreflang"], self.url(PRIVACY_BASIC, c)) for c in self.order] + [("x-default", "/privacy")]
        head = self.head(code="es", title=f"{title} · SofIA", description=description,
                         canonical=f"/{page}", alternates=alternates)
        return self.document(code="es", head=head, body=f"{header}\n<main>\n{main}\n</main>\n{footer}",
                             data={"words": {"carousel": "carrusel", "card": "Ir a la tarjeta"}})

    def router(self) -> list[dict]:
        """`DeeplinkBusiness.routes` (snapshot) in the app's order, as anchored regexes. The first route
        that matches wins, as in `DeeplinkBusiness.resolve`; a `webless` route matches and shows the
        not-found page, so `/entry/quick` is never read as the entry whose id is «quick»."""
        out = []
        for r in self.routes["routes"]:
            parts, rx = r["path"].split("/"), ""
            for p in parts:
                if p.startswith(":"):
                    name, optional = p[1:].rstrip("?"), p.endswith("?")
                    seg = f"/(?<{name}>[^/]+)"
                    rx += f"(?:{seg})?" if optional else seg
                else:
                    rx += "/" + re.escape(p)
            out.append({"re": f"^{rx}$", "requires": r["requires"], "path": r["path"], "action": r["action"],
                        "web": r["path"] not in self.routes["webless"]})
        return out

    def build_404(self) -> str:
        meta, body_tpl = page_meta((SRC / "pages" / "404.html").read_text(encoding="utf-8"))
        main = self.fill(body_tpl, "es", "es", "es_es")
        header = self.chrome("header", "index", "es", "es", "es_es")
        footer = self.chrome("footer", "index", "es", "es", "es_es")
        keys = self.keys_in(self.chrome_tpl("header", "index", "es") + self.chrome_tpl("footer", "index", "es") + body_tpl)
        keys = sorted(set(keys) | {"legal.langnote"})
        dicts, tags = {}, {}
        for c in self.order:
            cfg = self.locales[c]
            dicts[c] = {k: self.text(cfg["file"], k, c, cfg["col"]) for k in keys}
            tags[c] = cfg["lang"]
            if "variant" in cfg:
                v = cfg["variant"]
                dicts[v["lang"]] = {k: self.text(v["file"], k, c, v["col"]) for k in keys}
                tags[v["lang"]] = v["lang"]
        head = self.head(code="es", title="SofIA", description=self.strip_title(self.dicts["es"]["meta.description"]),
                         canonical="", alternates=[], noindex=True, banner=False,
                         extra=("\n<script>(function(){var u=location.href.replace(/[\"<>]/g,'');"
                                f"document.write('<meta name=\"apple-itunes-app\" content=\"app-id={APP_STORE_ID}, app-argument='+u+'\">')}})();</script>"))
        data = {"router": self.router(), "dicts": dicts, "langTags": tags, "androidPackage": ANDROID_PACKAGE,
                "words": {"carousel": "carrusel", "card": "Ir a la tarjeta"}}
        return self.document(code="es", head=head, body=f"{header}\n<main>\n{main}\n</main>\n{footer}", data=data)

    def sitemap(self) -> str:
        rows = ['<?xml version="1.0" encoding="UTF-8"?>',
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">']
        for page in LOCALIZED_PAGES + [PRIVACY_BASIC]:
            for c in self.order:
                rows.append(f"  <url><loc>{HOST}{self.url(page, c)}</loc>")
                for c2 in self.order:
                    rows.append(f'    <xhtml:link rel="alternate" hreflang="{self.locales[c2]["hreflang"]}" href="{HOST}{self.url(page, c2)}"/>')
                rows.append(f'    <xhtml:link rel="alternate" hreflang="x-default" href="{HOST}{self.url(page, "es")}"/>')
                rows.append("  </url>")
        for page in LEGAL_PAGES:
            if page != "privacy":  # listed above, with the summaries as its alternates
                rows.append(f"  <url><loc>{HOST}/{page}</loc></url>")
        rows.append("</urlset>")
        return "\n".join(rows) + "\n"

    def build(self, out: Path) -> dict:
        """Generate everything into a sibling temporary folder and swap it in only when the whole
        build succeeded: a failed (e.g. --release) build leaves the previous output intact."""
        out = out.resolve()
        if out.exists() and out != (ROOT / "docs").resolve() and any(out.iterdir()) and not (out / "404.html").exists():
            raise BuildError(f"refusing to replace {out}: not docs/ and not a previous build")
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=".build-", dir=out.parent))
        try:
            info = self._build_into(tmp)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        if out.exists():
            shutil.rmtree(out)
        tmp.rename(out)
        return info

    def _build_into(self, out: Path) -> dict:
        files: dict[str, str] = {}
        for page in LOCALIZED_PAGES:
            for c in self.order:
                files[self.out_path(page, c)] = self.build_localized(page, c)
        for c in self.order:
            if c != "es":
                files[self.out_path(PRIVACY_BASIC, c)] = self.build_localized(PRIVACY_BASIC, c)
        for page in LEGAL_PAGES:
            files[f"{page}.html"] = self.build_legal(page)
        files["404.html"] = self.build_404()
        files["sitemap.xml"] = self.sitemap()
        files["robots.txt"] = f"User-agent: *\nAllow: /\n\nSitemap: {HOST}/sitemap.xml\n"
        for rel, content in sorted(files.items()):
            p = out / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        # Copied byte for byte: static files (CNAME, .well-known, app-ads.txt, .nojekyll), assets, frozen versions.
        for src_dir, dst in ((SRC / "static", out), (SRC / "assets", out / "assets"), (SRC / "frozen", out)):
            if src_dir.exists():
                shutil.copytree(src_dir, dst, dirs_exist_ok=True)
        return {"files": len(files), "pending": sorted(set(self.pending))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--release", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "docs"))
    args = ap.parse_args(argv)
    try:
        info = Site(args.release).build(Path(args.out))
    except BuildError as e:
        print(f"build failed: {e}", file=sys.stderr)
        return 1
    print(f"built {info['files']} pages into {args.out}" + (f"; pending: {', '.join(info['pending'])}" if info["pending"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
