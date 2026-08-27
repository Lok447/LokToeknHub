#!/usr/bin/env bash
set -euo pipefail

# Restore only into an isolated database or during an approved maintenance window.
if [[ -z "${TOKEN_DATABASE_URL:-}" || "${1:-}" == "" ]]; then
  echo "usage: TOKEN_DATABASE_URL=... CONFIRM_RESTORE=YES $0 backup.dump" >&2
  exit 2
fi
if [[ "${CONFIRM_RESTORE:-}" != "YES" ]]; then
  echo "refusing restore: set CONFIRM_RESTORE=YES explicitly" >&2
  exit 2
fi
if ! command -v pg_restore >/dev/null 2>&1; then
  echo "pg_restore is required" >&2
  exit 2
fi
backup_file="$1"
[[ -f "$backup_file" ]] || { echo "backup file not found: $backup_file" >&2; exit 2; }
if [[ -f "$backup_file.sha256" ]]; then
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum --check "$backup_file.sha256"
  elif command -v shasum >/dev/null 2>&1; then
    (cd "$(dirname "$backup_file")" && shasum -a 256 --check "$(basename "$backup_file").sha256")
  else
    echo "sha256sum or shasum is required to verify backup" >&2
    exit 2
  fi
fi
pg_restore --clean --if-exists --no-owner --dbname="$TOKEN_DATABASE_URL" "$backup_file"
echo "restored $backup_file"
