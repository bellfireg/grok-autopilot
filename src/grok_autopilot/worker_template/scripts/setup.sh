#!/usr/bin/env bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════════════════════════
# cf-mail-worker — Interactive Setup Wizard
# ═══════════════════════════════════════════════════════════════════════════════
# Usage: npm run setup  (or: bash scripts/setup.sh)
#
# This script will:
#   1. Check prerequisites (node, wrangler)
#   2. Login to Cloudflare
#   3. Create D1 database
#   4. Generate wrangler.toml
#   5. Run schema migration
#   6. Deploy the worker
#   7. Verify deployment
# ═══════════════════════════════════════════════════════════════════════════════

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${CYAN}  $1${NC}"; }
ok()   { echo -e "${GREEN}  ✅ $1${NC}"; }
warn() { echo -e "${YELLOW}  ⚠️  $1${NC}"; }
err()  { echo -e "${RED}  ❌ $1${NC}"; }

echo ""
echo -e "${BOLD}  📦 cf-mail-worker — Setup Wizard${NC}"
echo "  ────────────────────────────────────"
echo ""

# ─── Step 1: Prerequisites ───────────────────────────────────────────────────

log "Checking prerequisites..."

if ! command -v node &> /dev/null; then
    err "Node.js not found. Install: https://nodejs.org"
    exit 1
fi
ok "Node.js $(node --version)"

if ! command -v npx &> /dev/null; then
    err "npx not found (should come with Node.js)"
    exit 1
fi

# Install dependencies if needed
if [ ! -d "node_modules" ]; then
    log "Installing dependencies..."
    npm install --silent
    ok "Dependencies installed"
fi

# ─── Step 2: Collect config ──────────────────────────────────────────────────

echo ""
read -rp "$(echo -e "${BOLD}  [?] Worker name${NC} (default: cf-mail-worker): ")" WORKER_NAME
WORKER_NAME="${WORKER_NAME:-cf-mail-worker}"

read -rp "$(echo -e "${BOLD}  [?] Domain${NC} (your email domain, e.g. mydomain.com): ")" DOMAIN
if [ -z "$DOMAIN" ]; then
    warn "No domain provided, using placeholder"
    DOMAIN="example.com"
fi

DB_NAME="${WORKER_NAME}-db"

echo ""
log "Config:"
log "  Worker:  $WORKER_NAME"
log "  Domain:  $DOMAIN"
log "  DB:      $DB_NAME"
echo ""

# ─── Step 3: Wrangler login ──────────────────────────────────────────────────

log "Checking Cloudflare authentication..."

if npx wrangler whoami &> /dev/null 2>&1; then
    ok "Already logged in to Cloudflare"
else
    log "Opening browser for Cloudflare login..."
    npx wrangler login
    ok "Logged in to Cloudflare"
fi

# ─── Step 4: Create D1 database ──────────────────────────────────────────────

log "Creating D1 database '$DB_NAME'..."

DB_OUTPUT=$(npx wrangler d1 create "$DB_NAME" 2>&1) || true

# Extract database_id from output
DB_ID=$(echo "$DB_OUTPUT" | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1)

if [ -z "$DB_ID" ]; then
    # Maybe it already exists — try to find it
    warn "Could not extract DB ID (may already exist)"
    log "Listing D1 databases..."
    npx wrangler d1 list 2>&1 | grep "$DB_NAME" || true
    echo ""
    read -rp "$(echo -e "${BOLD}  [?] Paste database_id${NC} (from output above): ")" DB_ID
    if [ -z "$DB_ID" ]; then
        err "No database_id provided. Aborting."
        exit 1
    fi
fi

ok "Database ready: $DB_ID"

# ─── Step 5: Generate wrangler.toml ──────────────────────────────────────────

log "Generating wrangler.toml..."

cat > wrangler.toml << EOF
name = "$WORKER_NAME"
main = "src/index.js"
compatibility_date = "2024-12-01"

[vars]
DOMAIN = "$DOMAIN"

[[d1_databases]]
binding = "DB"
database_name = "$DB_NAME"
database_id = "$DB_ID"

[triggers]
crons = ["0 * * * *"]
EOF

ok "wrangler.toml written"

# ─── Step 6: Run schema migration ────────────────────────────────────────────

log "Running schema migration..."
npx wrangler d1 execute "$DB_NAME" --remote --file=./schema.sql
ok "Database tables created"

# ─── Step 7: Deploy ──────────────────────────────────────────────────────────

log "Deploying worker..."
DEPLOY_OUTPUT=$(npx wrangler deploy 2>&1)
WORKER_URL=$(echo "$DEPLOY_OUTPUT" | grep -oE 'https://[a-z0-9-]+\.[a-z0-9-]+\.workers\.dev' | head -1)

if [ -z "$WORKER_URL" ]; then
    WORKER_URL="https://$WORKER_NAME.<your-subdomain>.workers.dev"
    warn "Could not detect worker URL. Find it at: https://dash.cloudflare.com → Workers"
else
    ok "Deployed: $WORKER_URL"
fi

# ─── Step 8: Verify ──────────────────────────────────────────────────────────

log "Verifying deployment..."
sleep 3

HEALTH=$(curl -s "$WORKER_URL/api/health" 2>/dev/null || echo "offline")

if echo "$HEALTH" | grep -q '"status":"healthy"'; then
    ok "Health check passed!"
else
    warn "Health check inconclusive (may need a few seconds to propagate)"
fi

# ─── Done ────────────────────────────────────────────────────────────────────

echo ""
echo "  ────────────────────────────────────"
echo -e "  ${GREEN}${BOLD}🎉 Setup complete!${NC}"
echo "  ────────────────────────────────────"
echo ""
echo -e "  ${YELLOW}⚠️  IMPORTANT: Enable Email Routing${NC}"
echo "     1. Go to Cloudflare Dashboard → Your Domain"
echo "     2. Email → Routing Rules"
echo "     3. Catch-all address → Edit → Send to Worker"
echo "     4. Select: $WORKER_NAME"
echo "     5. Save"
echo ""
echo -e "  ${CYAN}Set this in qoder-autopilot:${NC}"
echo "  qoder-autopilot config set worker-url $WORKER_URL"
echo ""
