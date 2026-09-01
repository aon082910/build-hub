#!/usr/bin/env bash
# BuildHub container entrypoint.
#
# Responsibilities, in order:
#   1. seed /config on first run so the YAML files are editable from the host
#   2. match the container user to Unraid's PUID/PGID so appdata stays writable
#   3. drop privileges and exec the requested mode
set -euo pipefail

CONFIG_DIR="${BUILDHUB_CONFIG_DIR:-/config}"
MODE="${BUILDHUB_MODE:-serve}"
PORT="${BUILDHUB_PORT:-8000}"
PUID="${PUID:-99}"
PGID="${PGID:-100}"

log() { printf '[buildhub] %s\n' "$*"; }

umask "${UMASK:-022}"

# --- 1. seed config ---------------------------------------------------------
mkdir -p "$CONFIG_DIR"/{db,media,browser_profile,fixtures}

for f in config.yaml providers.yaml duty.yaml freight.yaml; do
    if [ ! -f "$CONFIG_DIR/$f" ]; then
        cp "/defaults/$f" "$CONFIG_DIR/$f"
        log "seeded $f into $CONFIG_DIR (edit it there; the image copy is ignored)"
    fi
done

# An .env is optional -- every setting also works as a plain container variable,
# which is how Unraid prefers to pass them. It exists for people who would rather
# keep secrets in a file than in the container template.
if [ ! -f "$CONFIG_DIR/.env" ]; then
    cat > "$CONFIG_DIR/.env" <<'EOF'
# Optional. Anything set here is also settable as a container variable in Unraid,
# and the container variable wins. Useful for keeping API keys out of the template.
# TRANSLATE_PROVIDER=claude
# ANTHROPIC_API_KEY=
# EBAY_CLIENT_ID=
# EBAY_CLIENT_SECRET=
# BUILDHUB_ADMIN_TOKEN=
# BUILDHUB_PROXY=
EOF
    log "created $CONFIG_DIR/.env (optional; container variables override it)"
fi

# --- 2. permissions ---------------------------------------------------------
# Unraid runs shares as nobody:users (99:100). Creating a matching user inside the
# container means files written to appdata stay editable from the host, which is
# the single most common cause of "permission denied" in a first-run Unraid app.
if [ "$(id -u)" = "0" ]; then
    if ! getent group "$PGID" >/dev/null 2>&1; then
        groupadd -g "$PGID" buildhub
    fi
    if ! getent passwd "$PUID" >/dev/null 2>&1; then
        useradd -u "$PUID" -g "$PGID" -d /app -s /bin/bash buildhub
    fi
    RUN_AS="$(getent passwd "$PUID" | cut -d: -f1)"

    # Only the app's own directories -- never a recursive chown of a user's whole
    # share, which on a large media library would take minutes and surprise them.
    chown -R "$PUID:$PGID" "$CONFIG_DIR" 2>/dev/null || \
        log "WARNING: could not chown $CONFIG_DIR; check the share's permissions"
    chown -R "$PUID:$PGID" /app 2>/dev/null || true

    log "running as ${RUN_AS} (${PUID}:${PGID}), TZ=${TZ:-Etc/UTC}"
    exec setpriv --reuid "$PUID" --regid "$PGID" --init-groups "$0" "$@"
fi

# --- 3. run -----------------------------------------------------------------
cd /app
python -m buildhub.cli init-db

case "$MODE" in
    serve)
        # Web UI plus the crawl scheduler in one process, which is what a single
        # container wants. Set BUILDHUB_MODE=web to run the UI alone.
        log "mode=serve (web UI + scheduler) on port ${PORT}"
        exec python -m buildhub.cli serve --host 0.0.0.0 --port "$PORT" --with-scheduler
        ;;
    web)
        log "mode=web (UI only, no scheduled crawling) on port ${PORT}"
        exec python -m buildhub.cli serve --host 0.0.0.0 --port "$PORT"
        ;;
    schedule)
        log "mode=schedule (crawler only, no web UI)"
        exec python -m buildhub.cli schedule
        ;;
    crawl)
        log "mode=crawl (one sweep, then exit)"
        exec python -m buildhub.cli crawl ${BUILDHUB_CRAWL_ARGS:-}
        ;;
    shell)
        exec /bin/bash
        ;;
    *)
        # Anything else is treated as CLI arguments, so the same image can run
        # one-off commands: `docker run ... buildhub selftest --site dhgate`.
        log "running: buildhub.cli $MODE $*"
        exec python -m buildhub.cli $MODE "$@"
        ;;
esac
