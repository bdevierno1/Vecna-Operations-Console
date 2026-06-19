# Rollback Runbook — Database (SQLite)

**Service:** `backend/vecna.db` — SQLite file managed by SQLAlchemy (`backend/app/database.py`, `backend/app/schema_migrate.py`)  
**Tier:** 0  
**Owner:** On-call engineer  
**Last tested:** see Git log on this file  
**Related alerts:** [SEV1 runbook](../sev1-runbook.md) · [SEV2 runbook](../sev2-runbook.md)

---

## 1. Rollback decision gate

Roll back (restore from backup) when **any** of the following is true:

| Signal | Threshold |
|--------|-----------|
| `GET /api/health/deep` reports `"database": false` | sustained >2 min |
| `sqlite3 vecna.db "PRAGMA integrity_check"` returns anything other than `ok` | any failure |
| Schema migration (`schema_migrate.py`) left the DB in an inconsistent state | confirmed |
| `Operation` rows missing expected columns after a migration | any occurrence |
| Application logs show `OperationalError` or `IntegrityError` on reads | sustained |

> **Note:** SQLite serializes writes. High write contention causing timeouts is a scaling issue, not a rollback trigger.

---

## 2. Locate the database file

```bash
# Default path (relative to backend/)
REPO=/path/to/Vecna-Operations-Console/backend
DB="$REPO/vecna.db"

# Confirm file exists and is readable
ls -lh "$DB"
sqlite3 "$DB" "PRAGMA integrity_check;"  # expect: ok
```

If `DATABASE_URL` is set in `.env`, it overrides the default path — check `.env` first.

---

## 3. Pre-rollback: take a snapshot of the broken state

```bash
# Preserve the broken DB for post-incident forensics
cp "$DB" "${DB}.broken.$(date +%Y%m%dT%H%M%S)"
```

---

## 4. Rollback steps

### 4a. Restore from a timestamped backup

The recommended backup strategy is a periodic `cp vecna.db vecna.db.backup.<timestamp>` or a cron-driven `sqlite3 vecna.db ".backup vecna.db.backup"`. If that is in place:

```bash
# Stop the API server first to release the write lock
PID=$(pgrep -f "uvicorn app.main:app")
kill -SIGTERM "$PID"
sleep 3

# Restore the most recent backup
BACKUP=$(ls -t "$REPO"/vecna.db.backup.* 2>/dev/null | head -1)
if [ -z "$BACKUP" ]; then
  echo "ERROR: No backup found. Proceed to §4b (schema-only rollback)."
  exit 1
fi
echo "Restoring from: $BACKUP"
cp "$BACKUP" "$DB"

# Verify restored DB
sqlite3 "$DB" "PRAGMA integrity_check;"   # expect: ok
sqlite3 "$DB" "SELECT count(*) FROM operations;"

# Restart the API server
cd "$REPO"
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 &
sleep 3
curl -s http://127.0.0.1:8000/api/health/deep | jq '{db:.database}'
```

### 4b. Schema-only rollback (no data backup available)

If a migration added a column that breaks reads, and you only need to revert the schema (acceptable data loss: the new column's data):

```bash
# Identify columns added by the bad migration
sqlite3 "$DB" "PRAGMA table_info(operations);"
sqlite3 "$DB" "PRAGMA table_info(billing_sessions);"

# SQLite does not support DROP COLUMN natively in older versions.
# Recreate the table without the offending column:
# (Adjust column list to match the pre-migration schema from git history)
sqlite3 "$DB" "
BEGIN;
CREATE TABLE operations_restore AS SELECT <pre_migration_column_list> FROM operations;
DROP TABLE operations;
ALTER TABLE operations_restore RENAME TO operations;
COMMIT;
"

# Verify
sqlite3 "$DB" "PRAGMA integrity_check;"
```

> **Warning:** This destroys data in any dropped column. Prefer backup restore (§4a).

---

## 5. Verify rollback success

```bash
# 1. Integrity check
sqlite3 "$DB" "PRAGMA integrity_check;"          # expect: ok

# 2. Row counts look reasonable
sqlite3 "$DB" "SELECT count(*) FROM operations; SELECT count(*) FROM billing_sessions;"

# 3. API health
curl -s http://127.0.0.1:8000/api/health/deep | jq '{db:.database}'   # expect: true

# 4. Operations list is readable
curl -s http://127.0.0.1:8000/api/operations | jq 'length'
```

---

## 6. Backup commands (run these NOW if not already scheduled)

```bash
# One-time manual backup
sqlite3 "$DB" ".backup ${DB}.backup.$(date +%Y%m%dT%H%M%S)"

# Cron: every 15 minutes, keep 96 backups (~24 h)
# Add to crontab:
# */15 * * * * sqlite3 /path/to/vecna.db ".backup /path/to/backups/vecna.db.backup.$(date +\%Y\%m\%dT\%H\%M\%S)" && ls -t /path/to/backups/vecna.db.backup.* | tail -n +97 | xargs rm -f
```

---

## 7. Post-rollback actions

1. Announce in incident channel: "Database restored from `BACKUP`. Operations after `<backup timestamp>` may be lost."
2. Inform affected users their in-progress operations were cancelled.
3. File a postmortem using [docs/postmortem-template.md](../../postmortem-template.md).
4. Schedule a backup strategy review if this incident exposed a gap.

---

## 8. Rollback test log

| Date | Tester | Scenario | Result | Notes |
|------|--------|----------|--------|-------|
| YYYY-MM-DD | | | | Drill — first entry |
