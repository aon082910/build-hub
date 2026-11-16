# Build-Hub

Parts search and AI-assisted bill of materials for electronics and 3D-printer builds. Paste a parts list, or describe what you're building and let AI draft one, and Build-Hub finds the cheapest source for each line with a real purchase link. It prices on landed cost (unit price × minimum order + shipping), not a misleading unit price.

Why it's handy:

- **Searches many sources.** Marketplaces like AliExpress and eBay, plus distributors like LCSC, Mouser, Digi-Key and Octopart, into one local catalog.
- **Honest pricing.** The cheapest option is the cheapest after minimums and shipping.
- **Draft a BOM with AI**, or upload your own catalog.

Image: [allornothing/build-hub](https://hub.docker.com/r/allornothing/build-hub) · Source: [aon082910/build-hub](https://github.com/aon082910/build-hub)

## Install

**Unraid:** search **build-hub** in Apps, set an admin token, and keep `--shm-size=1g` in Extra Parameters. Without it the built-in browser crashes quietly and some sites return nothing. The full guide is in `UNRAID.md`.

**Docker:**

```bash
docker run -d --name build-hub -p 8000:8000 --shm-size=1g \
  -v ./appdata:/config \
  -e BUILDHUB_ADMIN_TOKEN=change-me \
  allornothing/build-hub:latest
```

Open `http://127.0.0.1:8000`.

| Setting | Default | What it's for |
|---|---|---|
| Port | `8000` | Web UI |
| `/config` | `/mnt/user/appdata/build-hub` | Database and settings |
| `BUILDHUB_ADMIN_TOKEN` | none | Admin access |
| `AI_PROVIDER` | `none` | Turns on AI-drafted BOMs (with `AI_API_KEY` or Ollama) |
| Distributor keys | none | Optional Octopart, Mouser, Digi-Key and eBay API keys |

## Before you crawl

Taobao, Tmall and 1688 need a logged-in session, and many sites will block fast scrapers or forbid it in their terms. The defaults are slow on purpose. Check each site's terms first.

## Help

[Open an issue](https://github.com/aon082910/build-hub/issues). Build-Hub grew out of Shopping Hub's engine. See `LICENSE` for the license.
