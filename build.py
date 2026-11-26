"""Build the BoringWare hub into dist/.

    python3 build.py            # published pages only (what deploy.sh uses)
    python3 build.py --drafts   # also drafts and future-dated articles, marked and noindexed, for previewing

Only dependency: the `markdown` package (BSD-3). Everything else is the standard library.
Content lives in content/; static files in static/ are copied as they are.
The build fails on a broken internal link, an image without alt text, or a bad article header.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import math
import re
import shutil
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import markdown

ROOT = Path(__file__).resolve().parent
CONTENT = ROOT / "content"
STATIC = ROOT / "static"
DIST = ROOT / "dist"

CFG = tomllib.loads((CONTENT / "site.toml").read_text(encoding="utf-8"))
SITE = CFG["site"]
PRODUCTS = {p["key"]: p for p in CFG["products"]}
BRAND_KEY = "boringware"  # `product = "boringware"` for articles about the brand itself

MD_EXTENSIONS = ["extra", "sane_lists", "admonition", "smarty", "toc"]
MD_CONFIG = {
    "toc": {"permalink": "#", "permalink_title": "Link to this section", "toc_depth": "2-3"},
    "smarty": {"smart_angled_quotes": False},
}

esc = html.escape
errors: list[str] = []
warnings: list[str] = []


# ---------------------------------------------------------------- helpers


def url(path: str) -> str:
    return SITE["url"] + path


def with_ref(href: str, ref: str | None = None) -> str:
    """Add ?ref= to a product-site link so Cloudflare Web Analytics can tell where visitors came from."""
    ref = ref or SITE["ref"]
    if "ref=" in href:
        return href
    base, _, frag = href.partition("#")
    joiner = "&" if "?" in base else "?"
    return f"{base}{joiner}ref={ref}" + (f"#{frag}" if frag else "")


PRODUCT_ORIGINS = sorted({"{0.scheme}://{0.netloc}".format(urlsplit(p["site"])) for p in PRODUCTS.values()})
PRODUCT_LINK_RE = re.compile(r'href="(' + "|".join(re.escape(o) for o in PRODUCT_ORIGINS) + r')([^"]*)"')


def tag_product_links(body: str) -> str:
    return PRODUCT_LINK_RE.sub(lambda m: 'href="' + with_ref(html.unescape(m.group(1) + m.group(2))).replace("&", "&amp;") + '"', body)


def nice_date(d: dt.date) -> str:
    return f"{d.day} {d.strftime('%B %Y')}"


def reading_minutes(text: str) -> int:
    return max(1, math.ceil(len(re.findall(r"\w+", text)) / 220))


def split_front_matter(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("+++\n"):
        raise ValueError(f"{path.relative_to(ROOT)}: must start with a +++ TOML header")
    head, _, body = text[4:].partition("\n+++\n")
    return tomllib.loads(head), body


def render_md(body: str) -> tuple[str, list]:
    md = markdown.Markdown(extensions=MD_EXTENSIONS, extension_configs=MD_CONFIG)
    out = md.convert(body)
    return out, md.toc_tokens


# ---------------------------------------------------------------- content model


@dataclass
class Article:
    src: Path
    title: str
    description: str
    date: dt.date
    product: str
    slug: str
    body_md: str
    updated: dt.date | None = None
    draft: bool = False
    toolbox: str = "short"  # short | full | none
    image: str = ""
    image_alt: str = ""
    html: str = ""
    toc: list = field(default_factory=list)

    @property
    def path(self) -> str:
        return f"/blog/{self.slug}/"

    @property
    def unpublished(self) -> bool:
        return self.draft or self.date > dt.date.today()


def load_articles(include_drafts: bool) -> list[Article]:
    items = []
    slugs: dict[str, Path] = {}
    for src in sorted((CONTENT / "articles").glob("*.md")):
        if src.name.startswith("_"):
            continue
        try:
            meta, body = split_front_matter(src)
        except Exception as exc:  # noqa: BLE001 - report every bad file, not just the first
            errors.append(str(exc))
            continue
        where = src.relative_to(ROOT)
        missing = [k for k in ("title", "description", "date", "product") if k not in meta]
        if missing:
            errors.append(f"{where}: missing {', '.join(missing)}")
            continue
        if meta["product"] not in PRODUCTS and meta["product"] != BRAND_KEY:
            errors.append(f"{where}: product must be one of {sorted(PRODUCTS) + [BRAND_KEY]}")
            continue
        if not isinstance(meta["date"], dt.date):
            errors.append(f"{where}: date must be a bare TOML date like 2026-12-01 (no quotes)")
            continue
        a = Article(
            src=src,
            title=meta["title"],
            description=meta["description"],
            date=meta["date"],
            product=meta["product"],
            slug=meta.get("slug", src.stem),
            body_md=body,
            updated=meta.get("updated"),
            draft=meta.get("draft", False),
            toolbox=meta.get("toolbox", "short"),
            image=meta.get("image", ""),
            image_alt=meta.get("image_alt", ""),
        )
        if a.toolbox not in ("short", "full", "none"):
            errors.append(f"{where}: toolbox must be short, full or none")
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", a.slug):
            errors.append(f"{where}: slug '{a.slug}' must be lowercase words joined by hyphens")
        if a.slug in slugs:
            errors.append(f"{where}: slug '{a.slug}' already used by {slugs[a.slug].name}")
        slugs[a.slug] = src
        if not 30 <= len(a.title) <= 70:
            warnings.append(f"{where}: title is {len(a.title)} characters (aim for 30-70 so search results don't cut it)")
        if not 70 <= len(a.description) <= 160:
            warnings.append(f"{where}: description is {len(a.description)} characters (aim for 70-160)")
        if a.image and not a.image_alt:
            errors.append(f"{where}: image needs image_alt")
        if a.unpublished and not include_drafts:
            continue
        a.html, a.toc = render_md(body)
        a.html = tag_product_links(a.html)
        items.append(a)
    items.sort(key=lambda a: (a.date, a.title), reverse=True)
    return items


# ---------------------------------------------------------------- page shell


def jsonld(obj: dict) -> str:
    # `</` must never appear inside a script element.
    return '<script type="application/ld+json">' + json.dumps(obj, ensure_ascii=False).replace("</", "<\\/") + "</script>"


ORG = {
    "@type": "Organization",
    "@id": url("/#org"),
    "name": SITE["name"],
    "url": url("/"),
    "logo": url("/img/boringware-logo-512.png"),
    "email": SITE["email"],
    "founder": {"@type": "Person", "name": SITE["author"]},
}


def layout(
    *,
    path: str,
    title: str,
    description: str,
    body: str,
    nav: str = "",
    og_type: str = "website",
    og_image: str = "/img/og-default.png",
    schema: list[dict] | None = None,
    noindex: bool = False,
    full_title: bool = False,
    extra_head: str = "",
    bare: bool = False,
) -> str:
    page_title = title if full_title else f"{title} | {SITE['name']}"
    gsv = SITE.get("google_site_verification", "")
    head = [
        "<!doctype html>",
        f'<html lang="{SITE["lang"]}">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{esc(page_title)}</title>",
        f'<meta name="description" content="{esc(description)}">',
        f'<link rel="canonical" href="{esc(url(path))}">',
        '<meta name="robots" content="noindex">' if noindex else "",
        f'<meta name="google-site-verification" content="{esc(gsv)}">' if gsv else "",
        f'<meta property="og:site_name" content="{esc(SITE["name"])}">',
        f'<meta property="og:type" content="{og_type}">',
        f'<meta property="og:title" content="{esc(title)}">',
        f'<meta property="og:description" content="{esc(description)}">',
        f'<meta property="og:url" content="{esc(url(path))}">',
        f'<meta property="og:image" content="{esc(url(og_image))}">',
        '<meta name="twitter:card" content="summary_large_image">',
        '<link rel="stylesheet" href="/style.css">',
        '<link rel="icon" href="/favicon.ico" sizes="48x48">',
        '<link rel="icon" href="/favicon.svg" type="image/svg+xml">',
        '<link rel="apple-touch-icon" href="/apple-touch-icon.png">',
        f'<link rel="alternate" type="application/atom+xml" title="{esc(SITE["name"])} articles" href="/feed.xml">',
        extra_head,
        *(jsonld({"@context": "https://schema.org", **s}) for s in (schema or [])),
        "</head>",
        "<body>",
    ]
    if bare:
        return "\n".join(x for x in head if x) + f"\n{body}\n</body>\n</html>\n"

    def nav_link(href: str, label: str, key: str) -> str:
        cur = ' aria-current="page"' if key == nav else ""
        return f'<a href="{href}"{cur}>{label}</a>'

    shell = f"""<a class="skip" href="#main">Skip to content</a>
<div class="wrap">
<header class="top">
  <a class="brand" href="/"><img src="/favicon.svg" alt="" width="30" height="30">{esc(SITE["name"])}</a>
  <nav aria-label="Main">{nav_link("/#tools", "Tools", "tools")}{nav_link("/blog/", "Articles", "blog")}{nav_link("/about/", "About", "about")}</nav>
</header>
<main id="main">
{body}
</main>
<footer class="site">
  <nav aria-label="Footer"><a href="/#tools">Tools</a><a href="/blog/">Articles</a><a href="/about/">About</a><a href="/privacy/">Privacy</a><a href="/feed.xml">Feed</a><a href="mailto:{esc(SITE["email"])}">{esc(SITE["email"])}</a></nav>
  <p>&copy; {dt.date.today().year} {esc(SITE["author"])} &middot; {esc(SITE["name"])}. {esc(SITE["tagline"])}.</p>
  <p>Microsoft, Windows, Excel, Word, PowerPoint and SharePoint are trademarks of the Microsoft group of companies. BoringWare is not affiliated with Microsoft or Adobe.</p>
</footer>
</div>"""
    return "\n".join(x for x in head if x) + "\n" + shell + "\n</body>\n</html>\n"


def breadcrumbs(trail: list[tuple[str, str]]) -> tuple[str, dict]:
    """trail: [(label, path)], last item is the current page."""
    items = []
    for i, (label, path) in enumerate(trail):
        if i == len(trail) - 1:
            items.append(f'<li aria-current="page">{esc(label)}</li>')
        else:
            items.append(f'<li><a href="{path}">{esc(label)}</a></li>')
    markup = f'<nav class="crumbs" aria-label="Breadcrumb"><ol>{"".join(items)}</ol></nav>'
    data = {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": label, "item": url(path)} for i, (label, path) in enumerate(trail)
        ],
    }
    return markup, data


def product_label(key: str) -> str:
    return PRODUCTS[key]["name"] if key in PRODUCTS else SITE["name"]


def post_list(articles: list[Article], show_tag: bool = True) -> str:
    if not articles:
        return ""
    rows = []
    for a in articles:
        tag = f'<span class="tag">{esc(product_label(a.product))}</span>' if show_tag else ""
        flag = ' <span class="tag">draft</span>' if a.unpublished else ""
        rows.append(
            f'<li><a class="title" href="{a.path}">{esc(a.title)}</a>{flag}'
            f'<p>{esc(a.description)}</p>'
            f'<span class="meta">{tag}<time datetime="{a.date.isoformat()}">{nice_date(a.date)}</time> &middot; {reading_minutes(a.body_md)} min read</span></li>'
        )
    return '<ul class="posts">' + "".join(rows) + "</ul>"


def product_card(p: dict) -> str:
    return f"""<div class="card product-card">
  <a href="/{p["key"]}/"><img class="shot" src="{p["image"]}" alt="{esc(p["image_alt"])}" loading="lazy" width="1280" height="800"></a>
  <h3><a href="/{p["key"]}/">{esc(p["name"])}</a></h3>
  <p>{esc(p["one_liner"])}</p>
  <p class="meta">{esc(p["price_text"])}</p>
  <p><a class="button" href="/{p["key"]}/">About {esc(p["name"])}</a><a class="button secondary" href="{esc(with_ref(p["site"]))}">Website</a></p>
</div>"""


# ---------------------------------------------------------------- pages


def page_home(articles: list[Article]) -> str:
    cards = "\n".join(product_card(p) for p in PRODUCTS.values())
    latest = post_list(articles[:5])
    latest_section = (
        f'<h2 id="latest">Latest articles</h2>\n{latest}\n<p><a href="/blog/">All articles</a></p>' if latest else ""
    )
    body = f"""<h1>{esc(SITE["tagline"])}.</h1>
<p class="lead">Each BoringWare tool does one tedious job properly. It runs on your Windows PC, so your files never leave it, and you can try it free before you pay once.</p>
<p><a class="button" href="#tools">See the tools</a><a class="button secondary" href="/blog/">Read the articles</a></p>

<h2 id="tools">Tools</h2>
<div class="grid">
{cards}
</div>

{latest_section}

<h2>How every tool is built</h2>
<div class="grid">
  <div class="card"><h3>Your files stay on your PC</h3><p>No uploads and no cloud service. The only internet connection is the licence check.</p></div>
  <div class="card"><h3>Originals are left alone</h3><p>Results are written as new files, and nothing is replaced without asking you first.</p></div>
  <div class="card"><h3>No subscription needed</h3><p>A free way to try each tool on your own files, then a one-time licence.</p></div>
</div>
"""
    website = {"@type": "WebSite", "@id": url("/#site"), "name": SITE["name"], "url": url("/"), "publisher": {"@id": url("/#org")}}
    return layout(
        path="/",
        title=f"{SITE['name']}: {SITE['tagline'].lower()}",
        full_title=True,
        description=SITE["description"],
        body=body,
        schema=[ORG, website],
    )


def page_product(p: dict, articles: list[Article]) -> str:
    related = [a for a in articles if a.product == p["key"]]
    crumbs, crumb_data = breadcrumbs([("Home", "/"), ("Tools", "/#tools"), (p["name"], f"/{p['key']}/")])
    store_btn = f'<a class="button secondary" href="{esc(p["store"])}">Microsoft Store</a>' if p.get("store") else ""
    features = "".join(f"<li>{esc(f)}</li>" for f in p["features"])
    guides = (
        f'<h2 id="articles">Articles about this job</h2>\n{post_list(related, show_tag=False)}' if related else ""
    )
    trademark = f'<p class="fine">{esc(p["trademark"])}</p>' if p.get("trademark") else ""
    body = f"""{crumbs}
<h1>{esc(p["full_name"])}</h1>
<p class="lead">{esc(p["one_liner"])}</p>
<p><a class="button" href="{esc(with_ref(p["site"]))}">Visit the {esc(p["name"])} website</a><a class="button secondary" href="{esc(p["download"])}">Download</a>{store_btn}</p>
<p class="note">{esc(p["trial"])}.</p>

<figure class="shot"><img src="{p["image"]}" alt="{esc(p["image_alt"])}" width="1280" height="800" loading="lazy"></figure>

<h2>The problem</h2>
<p>{esc(" ".join(p["problem"].split()))}</p>

<h2>What it does</h2>
<ul class="ticks">{features}</ul>
<p class="note">{esc(p["limits"])}</p>

<h2>Who it's for</h2>
<p>{esc(p["audience"])}</p>

<h2>At a glance</h2>
<dl class="facts">
  <dt>Price</dt><dd>{esc(p["price_text"])}</dd>
  <dt>Try it</dt><dd>{esc(p["trial"])}</dd>
  <dt>Runs on</dt><dd>{esc(p["platform"])}</dd>
  <dt>Your files</dt><dd>Processed on your PC and never uploaded. The only internet connection is the licence check.</dd>
  <dt>Help</dt><dd><a href="{esc(with_ref(p["help"]))}">{esc(p["name"])} help and FAQ</a></dd>
</dl>
{guides}
{trademark}
"""
    app = {
        "@type": "SoftwareApplication",
        "name": p["full_name"],
        "description": p["one_liner"],
        "applicationCategory": p["category"],
        "operatingSystem": "Windows 10, Windows 11",
        "url": p["site"],
        "downloadUrl": p["download"],
        "image": url(p["image"]),
        "offers": {"@type": "Offer", "price": p["price"], "priceCurrency": "USD"},
        "publisher": {"@id": url("/#org")},
    }
    return layout(
        path=f"/{p['key']}/",
        title=f"{p['name']}: {p['one_liner'].rstrip('.')}",
        full_title=len(p["one_liner"]) > 45,
        description=f"{p['one_liner']} {p['trial']}. {p['platform']}",
        body=body,
        nav="tools",
        og_image=p.get("og_image", "/img/og-default.png"),
        schema=[ORG, app, crumb_data],
    )


def toolbox(a: Article) -> str:
    if a.toolbox == "none":
        return ""
    keys = [a.product] if a.product in PRODUCTS else list(PRODUCTS)
    parts = []
    for k in keys:
        p = PRODUCTS[k]
        extra = ""
        if a.toolbox == "full":
            extra = "<ul>" + "".join(f"<li>{esc(f)}</li>" for f in p["features"][:3]) + "</ul>"
        parts.append(
            f'<p><strong>{esc(p["name"])}</strong>: {esc(p["one_liner"])} {esc(p["trial"])}.</p>{extra}'
            f'<p><a class="button" href="{esc(with_ref(p["site"]))}">Try {esc(p["name"])} free</a></p>'
        )
    heading = "A tool that does this" if len(keys) == 1 else "The tools"
    return f"""<aside class="toolbox" aria-labelledby="toolbox-h">
<h2 id="toolbox-h">{heading}</h2>
{"".join(parts)}
<p class="disclose">Made by {esc(SITE["name"])}, who also publish this site. Everything above works without it.</p>
</aside>"""


def toc_html(tokens: list) -> str:
    def walk(items: list) -> str:
        return "<ul>" + "".join(
            f'<li><a href="#{t["id"]}">{t["name"]}</a>{walk(t["children"]) if t["children"] else ""}</li>' for t in items
        ) + "</ul>"

    if len(tokens) < 3:
        return ""
    return f'<details class="toc" open><summary>In this article</summary>{walk(tokens)}</details>'


def page_article(a: Article, articles: list[Article]) -> str:
    crumbs, crumb_data = breadcrumbs([("Home", "/"), ("Articles", "/blog/"), (a.title, a.path)])
    same = [x for x in articles if x is not a and x.product == a.product]
    other = [x for x in articles if x is not a and x.product != a.product]
    related = (same + other)[:3]
    related_html = f'<section class="related"><h2>Related articles</h2>{post_list(related)}</section>' if related else ""
    updated = (
        f' &middot; Updated <time datetime="{a.updated.isoformat()}">{nice_date(a.updated)}</time>'
        if a.updated and a.updated != a.date
        else ""
    )
    draft_banner = (
        '<p class="admonition warning"><strong>Draft preview.</strong> Not published: '
        + ("marked draft." if a.draft else f"scheduled for {nice_date(a.date)}.")
        + "</p>"
        if a.unpublished
        else ""
    )
    product_line = (
        f' &middot; About <a href="/{a.product}/">{esc(PRODUCTS[a.product]["name"])}</a>' if a.product in PRODUCTS and a.toolbox == "full" else ""
    )
    body = f"""{crumbs}
<article class="narrow">
{draft_banner}
<h1>{esc(a.title)}</h1>
<p class="byline">By {esc(SITE["author"])}, {esc(SITE["name"])} &middot; <time datetime="{a.date.isoformat()}">{nice_date(a.date)}</time>{updated} &middot; {reading_minutes(a.body_md)} min read{product_line}</p>
{toc_html(a.toc)}
{a.html}
{toolbox(a)}
</article>
{related_html}
"""
    image = a.image or (PRODUCTS[a.product].get("og_image") if a.product in PRODUCTS else "") or "/img/og-default.png"
    posting = {
        "@type": "BlogPosting",
        "headline": a.title,
        "description": a.description,
        "datePublished": a.date.isoformat(),
        "dateModified": (a.updated or a.date).isoformat(),
        "author": {"@type": "Person", "name": SITE["author"], "url": url("/about/")},
        "publisher": {"@id": url("/#org")},
        "mainEntityOfPage": url(a.path),
        "image": url(image),
    }
    return layout(
        path=a.path,
        title=a.title,
        full_title=len(a.title) > 50,
        description=a.description,
        body=body,
        nav="blog",
        og_type="article",
        og_image=image,
        schema=[ORG, posting, crumb_data],
        noindex=a.unpublished,
        extra_head=f'<meta property="article:published_time" content="{a.date.isoformat()}">',
    )


def page_blog(articles: list[Article]) -> str:
    sections = []
    for key, p in PRODUCTS.items():
        items = [a for a in articles if a.product == key]
        if items:
            sections.append(
                f'<h2 id="{key}">{esc(p["name"])}</h2><p class="note">{esc(p["one_liner"])} <a href="/{key}/">About the tool</a></p>'
                + post_list(items, show_tag=False)
            )
    brand = [a for a in articles if a.product == BRAND_KEY]
    if brand:
        sections.append(f'<h2 id="boringware">About {esc(SITE["name"])}</h2>{post_list(brand, show_tag=False)}')
    if not sections:
        sections.append(
            "<p>The first articles are on the way: finding every external link hidden in Excel workbooks, and splitting one combined PDF into separate, named invoices.</p>"
            '<p class="note">New articles also appear in the <a href="/feed.xml">feed</a>.</p>'
        )
    crumbs, crumb_data = breadcrumbs([("Home", "/"), ("Articles", "/blog/")])
    body = f"""{crumbs}
<h1>Articles</h1>
<p class="lead">Step-by-step fixes for tedious office jobs: broken links after a file server move, combined PDFs that need splitting and naming, and similar chores. Each article shows the manual way first.</p>
{"".join(sections)}
"""
    return layout(
        path="/blog/",
        title="Articles: fixes for tedious office jobs",
        description="Step-by-step guides to tedious office jobs: fixing Excel, Word and PowerPoint links after a file server move, and splitting combined PDFs into named files.",
        body=body,
        nav="blog",
        schema=[ORG, crumb_data],
    )


def page_links(articles: list[Article]) -> str:
    latest = "".join(
        f'<a href="{a.path}">{esc(a.title)}<small>{esc(product_label(a.product))} &middot; {nice_date(a.date)}</small></a>' for a in articles[:4]
    )
    tools = "".join(
        f'<a href="/{p["key"]}/">{esc(p["name"])}<small>{esc(p["one_liner"])}</small></a>' for p in PRODUCTS.values()
    )
    latest_block = f'<h2>Latest articles</h2><div class="stack">{latest}<a href="/blog/">All articles</a></div>' if latest else ""
    body = f"""<main id="main" class="links-page wrap">
  <img class="logo" src="/img/boringware-logo-512.png" alt="BoringWare logo" width="84" height="84">
  <h1>{esc(SITE["name"])}</h1>
  <p class="note">{esc(SITE["tagline"])}.</p>
  {latest_block}
  <h2>Tools</h2>
  <div class="stack">{tools}</div>
  <h2>More</h2>
  <div class="stack"><a href="/">Website</a><a href="/about/">About</a><a href="mailto:{esc(SITE["email"])}">Email us<small>{esc(SITE["email"])}</small></a></div>
</main>"""
    return layout(
        path="/links/",
        title="Links",
        description=f"{SITE['name']}: latest articles and tools. {SITE['tagline']}.",
        body=body,
        bare=True,
    )


def page_markdown(src: Path) -> tuple[str, str]:
    meta, body = split_front_matter(src)
    path = meta["path"]
    content, _ = render_md(body)
    crumbs, crumb_data = breadcrumbs([("Home", "/"), (meta["title"], path)])
    page = layout(
        path=path,
        title=meta["title"],
        description=meta["description"],
        body=f'{crumbs}\n<div class="narrow">\n<h1>{esc(meta["title"])}</h1>\n{tag_product_links(content)}\n</div>',
        nav=meta.get("nav", ""),
        schema=[ORG, crumb_data],
    )
    return path, page


def page_404() -> str:
    body = """<h1>Page not found</h1>
<p>That page doesn't exist, or it has moved.</p>
<p><a class="button" href="/">Home</a><a class="button secondary" href="/blog/">Articles</a></p>"""
    return layout(path="/404", title="Page not found", description="Page not found.", body=body, noindex=True)


# ---------------------------------------------------------------- feeds and sitemaps


def sitemap(entries: list[tuple[str, dt.date | None]]) -> str:
    rows = []
    for path, lastmod in entries:
        lm = f"<lastmod>{lastmod.isoformat()}</lastmod>" if lastmod else ""
        rows.append(f"  <url><loc>{esc(url(path))}</loc>{lm}</url>")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(rows) + "\n</urlset>\n"


def feed(articles: list[Article]) -> str:
    def stamp(d: dt.date) -> str:
        return f"{d.isoformat()}T09:00:00Z"

    newest = max(((a.updated or a.date) for a in articles), default=dt.date.today())
    entries = "".join(
        f"""
  <entry>
    <title>{esc(a.title)}</title>
    <link href="{esc(url(a.path))}"/>
    <id>{esc(url(a.path))}</id>
    <published>{stamp(a.date)}</published>
    <updated>{stamp(a.updated or a.date)}</updated>
    <summary>{esc(a.description)}</summary>
  </entry>"""
        for a in articles[:20]
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>{esc(SITE["name"])} articles</title>
  <subtitle>{esc(SITE["tagline"])}</subtitle>
  <link href="{esc(url("/feed.xml"))}" rel="self"/>
  <link href="{esc(url("/"))}"/>
  <id>{esc(url("/"))}</id>
  <updated>{stamp(newest)}</updated>
  <author><name>{esc(SITE["author"])}</name></author>{entries}
</feed>
"""


# ---------------------------------------------------------------- checks


def check_output() -> None:
    """Every internal href/src must resolve to a built file; every <img> needs alt; same-page anchors must exist."""
    attr_re = re.compile(r'\s(href|src)="([^"]*)"')
    for page in DIST.rglob("*.html"):
        text = page.read_text(encoding="utf-8")
        rel = page.relative_to(DIST)
        ids = set(re.findall(r'\sid="([^"]+)"', text))
        for img in re.findall(r"<img\b[^>]*>", text):
            if " alt=" not in img:
                errors.append(f"dist/{rel}: <img> without alt: {img[:80]}")
        for _, target in attr_re.findall(text):
            target = html.unescape(target)
            if target.startswith("#"):
                if target[1:] and target[1:] not in ids:
                    errors.append(f"dist/{rel}: anchor {target} not found on the page")
                continue
            if not target.startswith("/") or target.startswith("//"):
                continue
            path = target.split("#")[0].split("?")[0]
            candidates = [DIST / path.lstrip("/")]
            if path.endswith("/"):
                candidates = [DIST / path.lstrip("/") / "index.html"]
            else:
                candidates += [DIST / (path.lstrip("/") + ".html"), DIST / path.lstrip("/") / "index.html"]
            if not any(c.is_file() for c in candidates):
                errors.append(f"dist/{rel}: broken internal link {target}")
            frag = target.partition("#")[2]
            if frag and path in ("/", "") and frag not in set(re.findall(r'\sid="([^"]+)"', (DIST / "index.html").read_text(encoding="utf-8"))):
                errors.append(f"dist/{rel}: anchor {target} not found on the home page")


# ---------------------------------------------------------------- main


def write(path: str, text: str) -> None:
    out = DIST / path.lstrip("/")
    if path.endswith("/"):
        out = out / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--drafts", action="store_true", help="include drafts and future-dated articles (noindexed); never deploy this")
    args = ap.parse_args()

    articles = load_articles(args.drafts)
    if errors:
        print("\n".join("ERROR " + e for e in errors), file=sys.stderr)
        return 1

    if DIST.exists():
        shutil.rmtree(DIST)
    shutil.copytree(STATIC, DIST)
    published = [a for a in articles if not a.unpublished]
    sitemap_entries: list[tuple[str, dt.date | None]] = [("/", None), ("/blog/", max((a.updated or a.date for a in published), default=None))]

    write("/", page_home(published))
    write("/blog/", page_blog(articles))
    write("/links/", page_links(published))
    write("/404.html", page_404())
    for p in PRODUCTS.values():
        write(f"/{p['key']}/", page_product(p, published))
        sitemap_entries.append((f"/{p['key']}/", None))
    for src in sorted((CONTENT / "pages").glob("*.md")):
        path, page = page_markdown(src)
        write(path, page)
        sitemap_entries.append((path, None))
    for a in articles:
        write(a.path, page_article(a, articles))
        if not a.unpublished:
            sitemap_entries.append((a.path, a.updated or a.date))

    write("/sitemap.xml", sitemap(sitemap_entries))
    write("/feed.xml", feed(published))
    write("/robots.txt", f"User-agent: *\nAllow: /\n\nSitemap: {url('/sitemap.xml')}\n")

    check_output()
    for w in warnings:
        print("warning " + w)
    if errors:
        print("\n".join("ERROR " + e for e in errors), file=sys.stderr)
        return 1
    drafts = sum(a.unpublished for a in articles)
    print(f"built dist/: {len(published)} published article(s)" + (f", {drafts} draft/scheduled preview(s) (noindex, DO NOT DEPLOY)" if drafts else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
