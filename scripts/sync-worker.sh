#!/usr/bin/env bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════════════════════════
# sync-worker.sh — Sync worker_template from cf-mail-worker repo
# ═══════════════════════════════════════════════════════════════════════════════
# Usage: bash scripts/sync-worker.sh
#
# This script clones the cf-mail-worker repo and copies the latest worker
# source files into src/qoder_autopilot/worker_template/.
#
# Run this when cf-mail-worker has updates that need to be bundled
# in qoder-autopilot's deploy command.
# ═══════════════════════════════════════════════════════════════════════════════

REPO="https://github.com/Daivageralda/cf-mail-worker.git"
TMPDIR=$(mktemp -d)
TARGET="src/qoder_autopilot/worker_template"

echo "📦 Syncing worker_template from cf-mail-worker..."
echo "   Source: $REPO"
echo "   Target: $TARGET"
echo ""

# Clone (shallow)
git clone --depth 1 "$REPO" "$TMPDIR" 2>/dev/null

# Copy worker files
mkdir -p "$TARGET/src/handlers" "$TARGET/scripts"

cp "$TMPDIR/src/index.js"            "$TARGET/src/index.js"
cp "$TMPDIR/src/config.js"           "$TARGET/src/config.js"
cp "$TMPDIR/src/utils.js"            "$TARGET/src/utils.js"
cp "$TMPDIR/src/handlers/api.js"     "$TARGET/src/handlers/api.js"
cp "$TMPDIR/src/handlers/email.js"   "$TARGET/src/handlers/email.js"
cp "$TMPDIR/schema.sql"              "$TARGET/schema.sql"
cp "$TMPDIR/package.json"            "$TARGET/package.json"
cp "$TMPDIR/wrangler.toml.example"   "$TARGET/wrangler.toml.example"
cp "$TMPDIR/scripts/setup.sh"        "$TARGET/scripts/setup.sh"

chmod +x "$TARGET/scripts/setup.sh"

# Cleanup
rm -rf "$TMPDIR"

# Show changes
echo "✅ Synced files:"
find "$TARGET" -type f | sort | sed 's/^/   /'
echo ""
echo "Run 'git diff' to review changes, then commit."
