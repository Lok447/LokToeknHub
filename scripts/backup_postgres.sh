#!/usr/bin/env bash
set -euo pipefail

# Run from a trusted host with pg_dump installed. Keep backups outside the app volume.
if [[ -z "${TOKEN_DATABASE_URL:-}" ]]; then
  echo "TOKEN_DATABASE_URL is required" >&2
  exit 2
fi
if ! command -v pg_dump >/dev/null 2>&1; then
  echo "pg_dump is required" >&2
  exit 2
fi

backup_dir="${TOKEN_BACKUP_DIR:-./backups}"
mkdir -p "$backup_dir"
umask 077
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_file="$backup_dir/loktoken-$timestamp.dump"
database_url="$(printf '%s' "$TOKEN_DATABASE_URL" | sed 's#^postgresql+psycopg://#postgresql://#')"
pg_dump --format=custom --no-owner --file="$backup_file" "$database_url"
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "$backup_file" > "$backup_file.sha256"
elif command -v shasum >/dev/null 2>&1; then
  shasum -a 256 "$backup_file" > "$backup_file.sha256"
else
  echo "sha256sum or shasum is required" >&2
  rm -f "$backup_file"
  exit 2
fi
echo "created $backup_file"
