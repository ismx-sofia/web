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
python3 tools/build.py --release --sign  # …and signs /documents.json (sops + Israel's age key)
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

## Legal versions: `/documents`

Every published version of a legal document is registered in `src/data/documents.json` and frozen in
`src/documents/<doc>/<YYMMDD>.html` (translations in `src/documents/<doc>/<lang>/`). A frozen file is the
exact page that was in force; its sha256 is what `core` records as proof of what a person was shown, so
it never changes: the build refuses one whose bytes no longer match the registry.

- `version` is `YYMMDD`, the Europe/Madrid date of `effective` (one version per document and day).
- `material` decides whether the app asks to accept it again («Requiere aceptación» / «Cambio menor»).
- `changes` is «what changes», per i18n file; a first version has none. The release build refuses a
  version without it in every published language.
- `legacy` is the old URL (`/privacy-v6`…) a version was published at before `/documents`; it keeps
  serving the same bytes.

The build publishes `/documents/` (the index), `/documents/<doc>/` (opens the version in force, or the
announced one), `/documents/<doc>/<YYMMDD>` (the version's page, which frames the original),
`/documents/<doc>/original/<YYMMDD>` (the frozen bytes) and `/documents.json`, the manifest `core`
ingests. Which version is in force depends on today's date, so `site.js` decides it in the browser.

### Signing the manifest

`--release --sign` also writes `/documents.json.sig`: an Ed25519 signature of `documents.json`, base64. The
private key is `secrets/documents-signing.sops.pem`, encrypted with SOPS for Israel's age key only
(`.sops.yaml`); the build decrypts it with `sops` into a pipe, so the key never touches the disk. The
public half is `secrets/documents-signing.pub.pem`, and `core` refuses a manifest whose signature does
not verify with it.

```
sops -d --input-type binary --output-type binary secrets/documents-signing.sops.pem | openssl pkey -pubout
```

prints the public key: it must match the `.pub.pem` (a way to check the age key decrypts it).
