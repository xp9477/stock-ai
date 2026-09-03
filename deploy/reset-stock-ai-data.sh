#!/usr/bin/env bash
set -euo pipefail

# Reset only the Stock AI application database.  The EMT bridge directory is
# deliberately outside this script's deletion set and is never touched.
#
# Usage:
#   ./deploy/reset-stock-ai-data.sh              # preview only
#   ./deploy/reset-stock-ai-data.sh --yes        # delete database files
#   STOCK_AI_DATA_DIR=/path/to/data ./deploy/reset-stock-ai-data.sh --yes

DATA_DIR="${STOCK_AI_DATA_DIR:-/var/lib/stock-ai/data}"
DATA_DIR="$(realpath -m -- "$DATA_DIR")"

case "${1:-}" in
  ""|"--dry-run"|"--yes") ;;
  *)
    echo "Usage: $0 [--dry-run|--yes]" >&2
    exit 2
    ;;
esac

if [[ "$#" -gt 1 ]]; then
  echo "Usage: $0 [--dry-run|--yes]" >&2
  exit 2
fi

if [[ "$DATA_DIR" == "/" || "$DATA_DIR" == "/var" || "$DATA_DIR" == "/var/lib" || "$DATA_DIR" == "/var/lib/stock-ai" ]]; then
  echo "Refusing an unsafe data directory: $DATA_DIR" >&2
  exit 2
fi

DB_PATH="$DATA_DIR/stock_ai.db"
FILES=(
  "$DB_PATH"
  "$DB_PATH-wal"
  "$DB_PATH-shm"
  "$DB_PATH-journal"
)

if command -v docker >/dev/null 2>&1; then
  if docker ps --format '{{.Names}}' | grep -Fxq 'stock-ai'; then
    echo "The stock-ai container is running. Stop it before resetting the database." >&2
    exit 3
  fi
fi

echo "Application database target: $DB_PATH"
echo "The EMT snapshot directory is not modified by this script."
echo "Files that would be removed:"
for file in "${FILES[@]}"; do
  if [[ -e "$file" ]]; then
    stat -c '  %n (%s bytes)' -- "$file"
  else
    echo "  $file (absent)"
  fi
done

if [[ "${1:-}" != "--yes" ]]; then
  echo
  echo "Preview only. Re-run with --yes after stopping Stock AI to delete these files."
  exit 0
fi

if [[ ! -d "$DATA_DIR" ]]; then
  echo "Data directory does not exist; nothing to delete: $DATA_DIR"
  exit 0
fi

for file in "${FILES[@]}"; do
  if [[ -e "$file" ]]; then
    rm -- "$file"
    echo "Removed $file"
  fi
done

echo
echo "Stock AI application data reset. Start the service to let init_db create a fresh schema and seed the official strategy."
