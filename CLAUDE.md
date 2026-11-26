# CLAUDE.md: BoringWare hub site

The public hub at https://boringware.pages.dev: brand home, one page per product, and the SEO
articles from the marketing plan (`../boringware-marketing-plan.md`, outside this repo). This repo
is **public**: never commit anything private (Dodo IDs beyond public checkout pages, secrets,
customer data, internal plans, real customer files).

## Rules

- **Static only.** No Pages Functions, no JavaScript of our own, no third-party fonts or scripts.
  The only outside script allowed is Cloudflare Web Analytics (see `static/_headers` CSP).
- **Product facts come from the product sites.** Before changing `content/site.toml`, check the
  product's own landing page and help page. Never claim a feature, a review, a customer count or a
  Store listing that doesn't exist.
- **Trademarks:** call the first product "Link Migrator" (never "Office Link Migrator" in public
  copy). Keep the Microsoft trademark notice.
- **Articles** follow the plan's format: the problem → the manual fix that works → where the manual
  fix breaks down → tool box (automatic) → related articles (automatic). They must be useful
  without the product. Disclosure is built into the tool box.
- **Tool box level:** `toolbox = "short"` until phase 3 (4 Jan 2027), then `"full"`.
- **Problems and FAQ** live in `content/site.toml` (`[[products.problems]]`, `[[products.faq]]`). Phrase
  each `question` the way people search (the exact error text or a "How do I..." question), and give
  why / by hand / with the tool. When an article covers a problem in depth, set `article = "<slug>"`.
- **Every link to a product site carries `?ref=`.** build.py adds `?ref=hub` automatically.
- **Accuracy over volume:** every step in an article must be one you have verified or can cite.
  Say which Excel/Acrobat versions a step applies to when it matters.

## Commands

```bash
python3 build.py            # must pass (no ERROR lines) before every commit
python3 build.py --drafts   # preview only; never deploy this output
./deploy.sh                 # build + `wrangler pages deploy` to project "boringware"
```

## Git

- Identity: `Aayush Choudhary` with the GitHub noreply address (public repo; set in the repo config).
- Work on `main` is fine for articles; never force-push `main`.
