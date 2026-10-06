# www.sofia.ismx.app

The SofIA website. SofIA is a brand of PROGRAMSAN, S.L.

- `src/` is the source: page templates (`pages/`), shared parts (`templates/`), texts per language
  (`i18n/`), legal documents in Spanish (`legal/`), data snapshots (`data/`), assets and the files
  served as they are (`static/`: `CNAME`, `.well-known/`, `app-ads.txt`).
- `docs/` is generated and is what GitHub Pages serves (Settings › Pages › Branch `main`, folder `/docs`).
  Never edit it by hand.

```
python3 tools/build.py            # draft build into docs/ (pending fields highlighted)
python3 tools/build.py --release  # what gets published: fails if anything is pending
python3 tools/test_build.py       # contract of the build (SOFIA_MONOREPO=…/03-code to check the app routes too)
tools/check_live.sh               # after a deployment, against the live site
```

Languages: Spanish at the root; `/en/ /ca/ /fr/ /pt/ /de/ /it/ /zh/ /ja/`. `/pt/` is prerendered in
Brazilian Portuguese and switches to European Portuguese in the browser. Translations are adapted,
not literal: see the brand guide in the project knowledge base (`07-identidad-y-comunicacion.md`).

`src/data/routes.json` mirrors `DeeplinkBusiness.routes` of the app and `src/data/app-labels.json` the
Settings labels of `parameter_i18n`; update them when those change.

Legal documents are published in Spanish only, at the root (`/privacy`, `/terms`, `/ia`, `/normas`,
`/cookies`, `/legal`). Each source in `src/legal/` starts with
`<!-- page: x · title: … · description: … · version: N · lede: … -->` and is written as
`<section id="…" data-title="…">` blocks: the build adds the hero, the numbered headings, the table of
contents and a column label to every table cell. `{path:a,b}` renders an app menu path from
`app-labels.json`. Every other language gets a privacy summary at `/<lang>/privacy`
(`src/pages/privacy-basic.html`, keys `pb.*`), linked from its footer and pointing to the Spanish text.
