import os
import shutil
import sqlite3
from datetime import datetime

DB_FILE = "data/coffee_app.db"  # change if needed


def table_exists(conn, table_name):
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    return cur.fetchone() is not None


def get_columns(conn, table_name):
    cur = conn.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in cur.fetchall()]  # row[1] = column name


def safe_add_column(conn, table_name, column_sql, col_name):
    cols = get_columns(conn, table_name)
    if col_name in cols:
        print(f"[SKIP] {table_name}.{col_name} already exists")
        return
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")
    print(f"[ADD ] {table_name}.{col_name}")


def ensure_backup(db_file):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{db_file}.bak_{ts}"
    shutil.copy2(db_file, backup_path)
    print(f"[BACKUP] Created: {backup_path}")


def get_first_group_id(conn):
    if not table_exists(conn, "groups"):
        return None
    cur = conn.execute("SELECT id FROM groups ORDER BY id LIMIT 1")
    row = cur.fetchone()
    return row[0] if row else None


def main():
    if not os.path.exists(DB_FILE):
        raise FileNotFoundError(f"DB not found: {DB_FILE}")

    ensure_backup(DB_FILE)

    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row

    try:
        # Basic table checks
        for tbl in ["shops", "reviews", "groups"]:
            if not table_exists(conn, tbl):
                print(f"[WARN] Table missing: {tbl}")

        if table_exists(conn, "shops"):
            safe_add_column(conn, "shops", "active INTEGER DEFAULT 1", "active")
            safe_add_column(conn, "shops", "source TEXT DEFAULT 'osm'", "source")
            safe_add_column(conn, "shops", "group_id INTEGER", "group_id")

            # Backfill active/source/group_id where NULL
            conn.execute("UPDATE shops SET active=1 WHERE active IS NULL")
            conn.execute("UPDATE shops SET source='osm' WHERE source IS NULL")
            print("[FILL] shops.active/source NULL backfilled")

        if table_exists(conn, "reviews"):
            safe_add_column(conn, "reviews", "group_id INTEGER", "group_id")

        # Backfill group_id if possible
        gid = get_first_group_id(conn)
        if gid is not None:
            if table_exists(conn, "shops"):
                conn.execute("UPDATE shops SET group_id=? WHERE group_id IS NULL", (gid,))
                print(f"[FILL] shops.group_id -> {gid} where NULL")
            if table_exists(conn, "reviews"):
                conn.execute("UPDATE reviews SET group_id=? WHERE group_id IS NULL", (gid,))
                print(f"[FILL] reviews.group_id -> {gid} where NULL")
        else:
            print("[WARN] No groups found, could not backfill group_id automatically")

        conn.commit()
        print("\n✅ Migration completed successfully.")

    except Exception as e:
        conn.rollback()
        print(f"\n❌ Migration failed: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()