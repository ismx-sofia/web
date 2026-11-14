// The example entries of the home page, drawn by core's share card renderer.
//
// The page shows entries as the app shows them: the cards come out of `CardTree`, the same code that draws
// the share card of `/graph/share/entry`, so a change in the app's card reaches the web the next time this
// runs. What goes on each card is `src/data/cards.json`; every text is a key of `src/i18n`, one image per
// card and per language in `src/assets/img/cards/<file>/` (`<file>` is the dictionary: es, en, …, pt-BR,
// pt-PT), plus the page's social image (`og.jpg`, 1200×630).
//
//   npx tsx --tsconfig ../../server/core/tsconfig.json tools/cards.mts
//
// It runs from the monorepo (`SOFIA_MONOREPO`, or this repository inside `03-code/apps/landing`): the
// renderer, its fonts and its images live in `server/core` and `apps/mobile/resources`, the backdrop of the social
// image in `libs/lib-design`.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import satori from "satori";
import { Resvg } from "@resvg/resvg-js";
import sharp from "sharp";

const LANDING = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const MONOREPO = path.resolve(process.env.SOFIA_MONOREPO ?? path.join(LANDING, "../.."));
const CORE = path.join(MONOREPO, "server/core");
const RESOURCES = path.join(MONOREPO, "apps/mobile/resources");
const OUT = path.join(LANDING, "src/assets/img/cards");

const card = (name: string) => path.join(CORE, "src/business/utils/card", name);
const { CARD, LIGHT, pt } = await import(card("CardSpec.ts"));
const { shadowSprite } = await import(card("CardShadow.ts"));
const { card: drawCard, header, MASKED } = await import(card("CardTree.ts"));
// The brand backdrop at any size (the banners of the stores use it too); core's `CardArt.backdrop` is drawn for 1080×1920.
const { backdrop } = await import(path.join(MONOREPO, "libs/lib-design/src/art/Backdrop.ts"));

interface Spec {
    id: string; type: "compose" | "record"; title?: string; excerpt?: string; date: string; stage: "brand" | "light";
    who?: string; anonymous?: string; place?: string; tags?: string; duration?: string; seed?: number;
    color?: string; font?: string;
}

/** The typefaces a card may use: the app's (REM) and the body of each example. Handlee has no bold. */
const FAMILIES: Record<string, [string, string]> = {
    REM: ["REM-Regular.ttf", "REM-Bold.ttf"], Gelasio: ["Gelasio-Regular.ttf", "Gelasio-Bold.ttf"],
    Handlee: ["Handlee-Regular.ttf", "Handlee-Regular.ttf"], Mali: ["Mali-Regular.ttf", "Mali-Bold.ttf"],
    Merienda: ["Merienda-Regular.ttf", "Merienda-Bold.ttf"],
};
const fonts = (body: string) => [...new Set(["REM", body])].flatMap((name) => {
    if (!FAMILIES[name]) throw new Error(`cards.json: no typeface «${name}»`);
    return FAMILIES[name].map((file, i) => ({
        name, data: fs.readFileSync(path.join(RESOURCES, "fonts", file)), weight: (i ? 700 : 400) as 400 | 700, style: "normal" as const,
    }));
});

const read = (file: string) => JSON.parse(fs.readFileSync(file, "utf8"));
const embed = (file: string, type: string) => `data:${type};base64,${fs.readFileSync(file).toString("base64")}`;
const slug = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "");

/** A key's text as the card prints it: no markup, no entities, and a title without its final full stop. */
function text(dict: Record<string, string>, key: string, file: string, title = false): string {
    const v = dict[key];
    if (v == null) throw new Error(`${file}.json: no key «${key}»`);
    const plain = v.replace(/<[^>]+>/g, "").replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").trim();
    return title ? plain.replace(/\.$/, "") : plain;
}

/** A recorded take's 96 buckets, drawn from a seed so every language shows the same wave. */
const waveform = (seed: number) => Array.from({ length: 96 }, (_, i) =>
    Math.round(28 + 62 * Math.abs(Math.sin(i * 0.37 + seed) * Math.cos(i * 0.11 + seed * 2))));

/** Everything the renderer brings that does not come from an entry (`ShareCardBusiness.init`). */
async function context() {
    const face = await sharp(path.join(RESOURCES, "images/default_profile_image.png"))
        .resize(Math.round(pt(108)), Math.round(pt(108)), { fit: "cover" }).png().toBuffer();
    return {
        ui: "REM", uiFile: path.join(RESOURCES, "fonts/REM-Regular.ttf"), body: "Gelasio",
        anonymousFace: embed(path.join(RESOURCES, "images/anonymous_profile_image.png"), "image/png"),
        defaultFace: `data:image/png;base64,${face.toString("base64")}`,
        placeTile: embed(path.join(CORE, "resources/card/place.jpg"), "image/jpeg"),
        musicTile: "",
        icon: embed(path.join(CORE, "resources/card/icon.png"), "image/png"),
        anonymousAuthor: "",
    };
}

/** The entry of one card in one language, as `CardData`. */
function data(spec: Spec, dict: Record<string, string>, file: string, tag: string, region: string) {
    const t = (key: string, title = false) => text(dict, key, file, title);
    const [name, city] = spec.who ? t(spec.who).split(" · ") : ["", ""];
    const placeCity = spec.place ? t(spec.place).split(" · ")[1] : null;
    const date = /^\d{4}-\d{2}-\d{2}$/.test(spec.date)
        ? new Date(`${spec.date}T12:00:00Z`).toLocaleDateString(tag, { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" })
        : t(spec.date);
    return {
        contentType: spec.type,
        title: spec.title ? t(spec.title, true) : "",
        date,
        handle: "sofia.ismx.app",
        color: spec.anonymous ? MASKED.color : spec.color ?? LIGHT.background,
        font: spec.anonymous ? MASKED.font : spec.font ?? "Gelasio",
        author: { name, nick: `${slug(name)}.${slug(city)}`, avatar: "" },
        isAnonymous: !!spec.anonymous,
        excerpt: spec.excerpt ? t(spec.excerpt) : null,
        place: placeCity ? { name: placeCity, address: new Intl.DisplayNames([tag], { type: "region" }).of(region) } : null,
        tags: spec.tags ? t(spec.tags).split(",").map((s) => s.trim()).filter(Boolean) : [],
        records: spec.type === "record" ? [{ waveform: waveform(spec.seed ?? 1), duration: spec.duration ?? "0:30" }] : [],
    };
}

/** Room around the card for its shadow, in px of the 900-px card. */
const MARGIN = { side: 60, top: 30, bottom: 96 };
const SHADOW = {
    brand: [{ blur: 46, y: 34, alpha: 0.34 }, { blur: 18, y: 10, alpha: 0.26 }],
    light: [{ blur: 46, y: 26, alpha: 0.12 }, { blur: 14, y: 6, alpha: 0.08 }],
};

/** One card as a transparent PNG: the author row on top when the example has an author. */
async function render(spec: Spec, entry: ReturnType<typeof data>, base: Awaited<ReturnType<typeof context>>, anonymous: string) {
    const f = fonts(entry.font);
    const ctx = { ...base, body: entry.font, anonymousAuthor: anonymous };
    // The baked shadow needs the card's height, which only a layout knows (`ShareCardBusiness.measure`).
    const alone = await satori(drawCard(entry, { ...ctx, cardShadow: undefined }), { width: CARD.width, fonts: f });
    const cardHeight = Number(alone.match(/height="(\d+(?:\.\d+)?)"/)?.[1] ?? 0);
    const cardShadow = await shadowSprite(CARD.width, pt(24), SHADOW[spec.stage]);
    const tree = {
        type: "div",
        props: {
            style: { display: "flex", flexDirection: "column", fontFamily: "REM",
                padding: `${MARGIN.top}px ${MARGIN.side}px ${MARGIN.bottom}px` },
            children: [
                ...(spec.who || spec.anonymous ? [header(entry, ctx)] : []),
                drawCard(entry, { ...ctx, cardShadow, cardHeight }),
            ],
        },
    };
    const svg = await satori(tree, { width: CARD.width + MARGIN.side * 2, fonts: f });
    return Buffer.from(new Resvg(svg).render().asPng());
}

/** The page's social image: the brand backdrop, the app icon, the name and one example card. */
async function social(cardPng: Buffer, base: Awaited<ReturnType<typeof context>>) {
    const W = 1200, H = 630;
    const meta = await sharp(cardPng).metadata();
    const w = 560, h = Math.round((meta.height! * w) / meta.width!);
    const lockup = { x: 84, y: 150, w: 460, h: 330 };
    const stage = new Resvg(backdrop({ composition: "marea", w: W, h: H, clear: [lockup] })).render().asPng();
    const tree = {
        type: "div",
        props: {
            style: { width: W, height: H, display: "flex", alignItems: "center", fontFamily: "REM",
                backgroundImage: `url(data:image/png;base64,${Buffer.from(stage).toString("base64")})`, backgroundSize: `${W}px ${H}px` },
            children: [
                { type: "div", props: { style: { display: "flex", flexDirection: "column", alignItems: "flex-start", marginLeft: 84, width: 460 }, children: [
                    { type: "img", props: { src: base.icon, style: { width: 132, height: 132, borderRadius: 34 } } },
                    { type: "div", props: { style: { marginTop: 26, fontSize: 112, fontWeight: 700, letterSpacing: -4, lineHeight: "112px", color: "#fff" }, children: "SofIA" } },
                ] } },
                { type: "img", props: { src: `data:image/png;base64,${cardPng.toString("base64")}`, style: { width: w, height: h, marginLeft: 20 } } },
            ],
        },
    };
    const svg = await satori(tree, { width: W, height: H, fonts: fonts("REM") });
    return sharp(Buffer.from(new Resvg(svg).render().asPng())).flatten({ background: "#3B7BFF" }).jpeg({ quality: 86, mozjpeg: true }).toBuffer();
}

const spec = read(path.join(LANDING, "src/data/cards.json")) as { cards: Spec[]; og: string };
const locales = read(path.join(LANDING, "src/data/locales.json"));
/** Every dictionary a page shows: the languages, and the European Portuguese the /pt/ page switches to. */
const files: { file: string; tag: string; region: string }[] = [];
for (const code of locales.order) {
    const cfg = locales.locales[code];
    files.push({ file: cfg.file, tag: cfg.og.replace("_", "-"), region: cfg.og.split("_")[1] });
    if (cfg.variant) files.push({ file: cfg.variant.file, tag: cfg.variant.og.replace("_", "-"), region: cfg.variant.og.split("_")[1] });
}

const base = await context();
fs.rmSync(OUT, { recursive: true, force: true });
for (const { file, tag, region } of files) {
    const dict = read(path.join(LANDING, "src/i18n", `${file}.json`));
    const dir = path.join(OUT, file);
    fs.mkdirSync(dir, { recursive: true });
    for (const s of spec.cards) {
        const anonymous = s.anonymous ? text(dict, s.anonymous, file) : "";
        const png = await render(s, data(s, dict, file, tag, region), base, anonymous);
        await sharp(png).webp({ quality: 90, alphaQuality: 90, effort: 6 }).toFile(path.join(dir, `${s.id}.webp`));
        if (s.id === spec.og) fs.writeFileSync(path.join(dir, "og.jpg"), await social(png, base));
    }
    console.log(`${file}: ${spec.cards.length} cards`);
}
