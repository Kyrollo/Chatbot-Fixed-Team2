#!/bin/bash
set -e

echo "════════════════════════════════════════════"
echo "🚀 Sprint 3: PostgreSQL 17 + Apache AGE"
echo "════════════════════════════════════════════"

# ─────────────────────────────
# 1. System update + dependencies
# ─────────────────────────────
echo "📦 Installing system dependencies..."

sudo apt update && sudo apt upgrade -y

sudo apt install -y \
curl ca-certificates wget gnupg lsb-release \
git build-essential make gcc \
flex bison libreadline-dev zlib1g-dev

# ─────────────────────────────
# 2. Add PostgreSQL repo (PGDG)
# ─────────────────────────────
echo "🐘 Adding PostgreSQL repository..."

sudo install -d /usr/share/postgresql-common/pgdg

wget -qO- https://www.postgresql.org/media/keys/ACCC4CF8.asc \
| sudo tee /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc > /dev/null

echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] \
https://apt.postgresql.org/pub/repos/apt jammy-pgdg main" | \
sudo tee /etc/apt/sources.list.d/pgdg.list

sudo apt update

# ─────────────────────────────
# 3. Install PostgreSQL 17
# ─────────────────────────────
echo "🐘 Installing PostgreSQL 17..."

sudo apt install -y \
postgresql-17 \
postgresql-client-17 \
postgresql-server-dev-17

# ─────────────────────────────
# 4. Start PostgreSQL
# ─────────────────────────────
echo "▶ Starting PostgreSQL..."

sudo service postgresql start

# ─────────────────────────────
# 5. Clone Apache AGE
# ─────────────────────────────
echo "📥 Cloning Apache AGE..."

cd ~
if [ ! -d "age" ]; then
  git clone https://github.com/apache/age.git
fi

cd age

# FIX: pinned to the official PG17-compatible release tag instead of
# `master`, which moves constantly and isn't guaranteed stable for PG17.
git fetch --tags
git checkout PG17/v1.6.0-rc0

# ─────────────────────────────
# 6. Build Apache AGE
# ─────────────────────────────
echo "⚙️ Building Apache AGE..."

make PG_CONFIG=/usr/lib/postgresql/17/bin/pg_config
sudo make install PG_CONFIG=/usr/lib/postgresql/17/bin/pg_config

# ─────────────────────────────
# 7. Configure PostgreSQL safely
# ─────────────────────────────
echo "⚙️ Configuring PostgreSQL..."

PG_CONF="/etc/postgresql/17/main/postgresql.conf"
PG_HBA="/etc/postgresql/17/main/pg_hba.conf"

# Port (avoid Windows conflict)
sudo sed -i "s/^#port = .*/port = 5434/" "$PG_CONF"
grep -q "^port = 5434" "$PG_CONF" || echo "port = 5434" | sudo tee -a "$PG_CONF"

# FIX: listen on all interfaces, not just localhost-inside-WSL2.
# Your Python services run on WINDOWS, outside WSL2 — with
# 'localhost' here, Postgres only accepts connections from inside
# the WSL2 box itself and Windows gets connection refused.
sudo sed -i "s/^#listen_addresses.*/listen_addresses = '*'/" "$PG_CONF"
sudo sed -i "s/^listen_addresses = 'localhost'/listen_addresses = '*'/" "$PG_CONF"

# Load AGE at startup
if grep -q "shared_preload_libraries" "$PG_CONF"; then
  sudo sed -i "s/^shared_preload_libraries.*/shared_preload_libraries = 'age'/" "$PG_CONF"
else
  echo "shared_preload_libraries = 'age'" | sudo tee -a "$PG_CONF"
fi

# FIX: allow connections from Windows via WSL2's virtual network,
# not just from 127.0.0.1/localhost (which is WSL2-internal only).
# This is still password-protected (md5), so it's not an open door —
# just reachable from the Windows side where your services live.
echo "host all all 127.0.0.1/32 md5"  | sudo tee -a "$PG_HBA"
echo "host all all localhost md5"     | sudo tee -a "$PG_HBA"
echo "host all all 0.0.0.0/0 md5"     | sudo tee -a "$PG_HBA"

# ─────────────────────────────
# 8. Restart PostgreSQL
# ─────────────────────────────
echo "🔄 Restarting PostgreSQL..."

sudo service postgresql restart

# ─────────────────────────────
# 9. Set password
# ─────────────────────────────
echo "🔐 Setting postgres password..."

# FIX: matches the existing PostgreSQL 16 password so the .env
# doesn't need two different credentials for two Postgres instances.
sudo -u postgres psql -c "ALTER USER postgres PASSWORD '1234';"

# ─────────────────────────────
# 10. Enable AGE
# ─────────────────────────────
echo "📊 Enabling Apache AGE..."

sudo -u postgres psql -c "CREATE EXTENSION IF NOT EXISTS age;"
sudo -u postgres psql -c "LOAD 'age';"
sudo -u postgres psql -c "SET search_path = ag_catalog, public;"

# ─────────────────────────────
# 11. Test graph creation
# ─────────────────────────────
echo "🧪 Testing AGE..."

sudo -u postgres psql -c "SELECT * FROM ag_catalog.create_graph('rag_graph');"

# ─────────────────────────────
# DONE
# ─────────────────────────────
echo "════════════════════════════════════════════"
echo "✅ SETUP COMPLETE!"
echo "👉 PostgreSQL: localhost:5434"
echo "👉 AGE: Enabled"
echo "👉 Graph: rag_graph created"
echo "════════════════════════════════════════════"
echo ""
echo "Next: verify from WINDOWS PowerShell (not WSL2) with:"
echo '  psql -h localhost -p 5434 -U postgres -c "SELECT version();"'
echo "This confirms Windows can actually reach the WSL2 database."
