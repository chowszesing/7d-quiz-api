"""
SQLite → PostgreSQL 数据迁移脚本
用於將現有 quiz_results.db 中的數據遷移到 PostgreSQL

用法：
  1. 確保 DATABASE_URL 環境變量已設為 Railway PostgreSQL 連接字串
  2. python migrate_to_pg.py

注意：此腳本不會刪除 SQLite 中的數據，可安全重複執行（會跳過已遷移的記錄）
"""

import os
import sqlite3
import json
import sys

# PostgreSQL 連接字串
DATABASE_URL = os.environ.get('DATABASE_URL', '')
if not DATABASE_URL:
    print("❌ 請設置 DATABASE_URL 環境變量")
    print("   在 Railway Dashboard → Variables → 添加 PostgreSQL 插件後自動生成")
    sys.exit(1)

# SQLite 數據庫路徑
SQLITE_PATH = os.environ.get('DATABASE', 'quiz_results.db')

if not os.path.exists(SQLITE_PATH):
    print(f"⚠️ SQLite 文件不存在: {SQLITE_PATH}，無需遷移")
    sys.exit(0)


def get_pg_conn():
    """連接到 PostgreSQL"""
    import psycopg2
    psycopg2.paramstyle = 'qmark'
    return psycopg2.connect(DATABASE_URL)


def get_sqlite_conn():
    """連接到 SQLite"""
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists_sqlite(conn, name):
    c = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return c.fetchone() is not None


def table_exists_pg(conn, name):
    c = conn.execute("SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name=?)", (name,))
    return c.fetchone()[0]


def count_sqlite(conn, table):
    c = conn.execute(f"SELECT COUNT(*) FROM {table}")
    return c.fetchone()[0]


def migrate_table(table, columns, insert_sql, transform_fn=None):
    """遷移單個表"""
    sl_conn = get_sqlite_conn()
    pg_conn = get_pg_conn()

    if not table_exists_sqlite(sl_conn, table):
        print(f"  ⏭️  SQLite 中無 {table} 表，跳過")
        sl_conn.close()
        pg_conn.close()
        return

    count = count_sqlite(sl_conn, table)
    if count == 0:
        print(f"  ⏭️  {table}: 0 條記錄，跳過")
        sl_conn.close()
        pg_conn.close()
        return

    rows = sl_conn.execute(f"SELECT {', '.join(columns)} FROM {table} ORDER BY id").fetchall()
    pg_cursor = pg_conn.cursor()

    migrated = 0
    for row in rows:
        try:
            params = tuple(row[c] for c in columns)
            if transform_fn:
                params = transform_fn(params)
            pg_cursor.execute(insert_sql, params)
            migrated += 1
        except Exception as e:
            print(f"  ⚠️  跳過重複記錄 (id={row['id'] if 'id' in columns else '?'}): {e}")
            pg_conn.rollback()
            pg_cursor = pg_conn.cursor()
            continue

    pg_conn.commit()
    pg_cursor.close()
    pg_conn.close()
    sl_conn.close()
    print(f"  ✅ {table}: 遷移 {migrated}/{count} 條記錄")


def main():
    print("=" * 50)
    print("SQLite → PostgreSQL 數據遷移")
    print("=" * 50)
    print(f"SQLite: {SQLITE_PATH}")
    print(f"PostgreSQL: {'***' + DATABASE_URL[-20:] if len(DATABASE_URL) > 20 else DATABASE_URL}")
    print()

    # 1. admin_user 表
    print("📋 遷移 admin_user ...")
    migrate_table(
        'admin_user',
        ['id', 'username', 'password_hash', 'role'],
        'INSERT INTO admin_user (id, username, password_hash, role) VALUES (?, ?, ?, ?) ON CONFLICT (username) DO UPDATE SET password_hash=EXCLUDED.password_hash'
    )

    # 2. quiz_results 表
    print("📋 遷移 quiz_results ...")
    migrate_table(
        'quiz_results',
        ['id', 'user_name', 'industry', 'experience', 'answers', 'question_order',
         'scores', 'validity_check', 'submitted_at', 'ip_address', 'user_agent'],
        'INSERT INTO quiz_results (id, user_name, industry, experience, answers, question_order, scores, validity_check, submitted_at, ip_address, user_agent) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING'
    )

    # 3. quiz_results_48 表
    print("📋 遷移 quiz_results_48 ...")
    columns_48 = ['id', 'user_name', 'experience', 'industry', 'answers',
                  'question_order', 'scores', 'submitted_at', 'ip_address',
                  'user_agent', 'access_token', 'personality_report']
    migrate_table(
        'quiz_results_48',
        columns_48,
        f'INSERT INTO quiz_results_48 ({", ".join(columns_48)}) VALUES ({", ".join(["?" for _ in columns_48])}) ON CONFLICT (id) DO NOTHING'
    )

    # 4. access_tokens 表
    print("📋 遷移 access_tokens ...")
    migrate_table(
        'access_tokens',
        ['token', 'used', 'assigned_to', 'created_at', 'used_at'],
        'INSERT INTO access_tokens (token, used, assigned_to, created_at, used_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT (token) DO NOTHING'
    )

    print()
    print("=" * 50)
    print("✅ 遷移完成！")
    print("=" * 50)
    print()
    print("下一步：確認 Railway 已設置 DATABASE_URL，然後重新部署應用。")
    print("部署後新數據將存儲在 PostgreSQL 中，部署不再丟失。")


if __name__ == '__main__':
    main()
