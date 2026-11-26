#!/usr/bin/env bash
# Build the published site and upload it to the Cloudflare Pages project "boringware"
# (https://boringware.pages.dev). Needs `npx wrangler login` once on this machine.
# Never deploy a --drafts build: this script always builds without it.
set -euo pipefail
cd "$(dirname "$0")"
python3 build.py
npx --yes wrangler@4 pages deploy dist --project-name boringware --branch main --commit-dirty=true
