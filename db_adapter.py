"""
数据库适配器 - 同时支持 SQLite 和 PostgreSQL
Railway/Render 环境优先使用 PostgreSQL，本地开发使用 SQLite
"""

import os
import sqlite3
import json
from contextlib import contextmanager

# 检测数据库类型
DATABASE_URL = os.environ.get('DATABASE_URL', '')
USE_POSTGRES = bool(DATABASE_URL)

# 连接池（PostgreSQL）
_pg_pool = None

def get_postgres_pool():
    """获取 PostgreSQL 连接池"""
    global _pg_pool
    if _pg_pool is None:
        import psycopg2
        from psycopg2 import pool
        _pg_pool = pool.ThreadedConnectionPool(
            minconn=1, maxconn=10,
            dsn=DATABASE_URL
        )
    return _pg_pool

def get_db():
    """
    统一数据库连接上下文管理器
    自动选择 SQLite 或 PostgreSQL
    """
    if USE_POSTGRES:
        return get_db_postgres()
    else:
        return get_db_sqlite()

@contextmanager
def get_db_sqlite():
    """SQLite 连接"""
    db_path = os.environ.get('DATABASE', 'quiz_results.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

@contextmanager
def get_db_postgres():
    """PostgreSQL 连接"""
    pg_pool = get_postgres_pool()
    conn = pg_pool.getconn()
    conn.autocommit = False
    try:
        yield conn
    finally:
        pg_pool.putconn(conn)

# ============ PostgreSQL 序列生成器 ============
def execute_with_pk(conn, sql, params=None, pk_column='id', table=None):
    """
    执行SQL并返回新增记录的主键
    PostgreSQL 使用 RETURNING，SQLite 使用 lastrowid
    """
    cursor = conn.cursor()
    if USE_POSTGRES:
        # PostgreSQL: 使用 RETURNING
        sql = sql.rstrip().rstrip(';')
        if not sql.upper().endswith('RETURNING ' + pk_column.upper()):
            if 'RETURNING' in sql.upper():
                sql = sql + f" RETURNING {pk_column}"
            else:
                sql = sql + f" RETURNING {pk_column}"
        cursor.execute(sql, params or ())
        row = cursor.fetchone()
        conn.commit()
        return row[0] if row else None
    else:
        # SQLite: 使用 lastrowid
        cursor.execute(sql, params or ())
        conn.commit()
        return cursor.lastrowid

# ============ JSON 序列化/反序列化 ============
def json_encode(data):
    """统一 JSON 序列化"""
    return json.dumps(data, ensure_ascii=False)

def json_decode(data):
    """统一 JSON 反序列化"""
    if data is None:
        return None
    if isinstance(data, str):
        return json.loads(data)
    return data

# ============ 数据库初始化 ============
def init_db():
    """初始化数据库表（自动适配 SQLite 或 PostgreSQL）"""
    
    if USE_POSTGRES:
        init_db_postgres()
    else:
        init_db_sqlite()

def init_db_sqlite():
    """SQLite 数据库初始化"""
    db_path = os.environ.get('DATABASE', 'quiz_results.db')
    with get_db_sqlite() as conn:
        c = conn.cursor()
        _create_tables_sqlite(c)
        conn.commit()
    print(f"[DB] SQLite 初始化完成: {db_path}")

def init_db_postgres():
    """PostgreSQL 数据库初始化"""
    with get_db_postgres() as conn:
        c = conn.cursor()
        _create_tables_postgres(c)
        conn.commit()
    print("[DB] PostgreSQL 初始化完成")

def _create_tables_sqlite(c):
    """SQLite 建表语句"""
    c.execute('''CREATE TABLE IF NOT EXISTS quiz_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_name TEXT, industry TEXT, experience TEXT,
        answers TEXT, question_order TEXT, scores TEXT,
        validity_check INTEGER, submitted_at TEXT,
        ip_address TEXT, user_agent TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS quiz_results_48 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_name TEXT, experience TEXT, industry TEXT,
        answers TEXT, question_order TEXT, scores TEXT,
        submitted_at TEXT, ip_address TEXT, user_agent TEXT,
        access_token TEXT,
        personality_report TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS access_tokens (
        token TEXT PRIMARY KEY,
        used INTEGER DEFAULT 0,
        assigned_to TEXT,
        created_at TEXT,
        used_at TEXT)''')
    
    # 迁移：给 quiz_results_48 添加 access_token 列
    try:
        c.execute("ALTER TABLE quiz_results_48 ADD COLUMN access_token TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在
    
    # 迁移：给 quiz_results_48 添加 personality_report 列
    try:
        c.execute("ALTER TABLE quiz_results_48 ADD COLUMN personality_report TEXT")
    except sqlite3.OperationalError:
        pass

def _create_tables_postgres(c):
    """PostgreSQL 建表语句"""
    c.execute('''CREATE TABLE IF NOT EXISTS quiz_results (
        id SERIAL PRIMARY KEY,
        user_name TEXT, industry TEXT, experience TEXT,
        answers TEXT, question_order TEXT, scores TEXT,
        validity_check INTEGER, submitted_at TEXT,
        ip_address TEXT, user_agent TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS quiz_results_48 (
        id SERIAL PRIMARY KEY,
        user_name TEXT, experience TEXT, industry TEXT,
        answers TEXT, question_order TEXT, scores TEXT,
        submitted_at TEXT, ip_address TEXT, user_agent TEXT,
        access_token TEXT,
        personality_report TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS access_tokens (
        token VARCHAR(64) PRIMARY KEY,
        used BOOLEAN DEFAULT FALSE,
        assigned_to TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        used_at TIMESTAMP)''')
    
    # PostgreSQL 使用 ALTER TABLE 添加列（IF NOT EXISTS 语法）
    try:
        c.execute("ALTER TABLE quiz_results_48 ADD COLUMN IF NOT EXISTS access_token TEXT")
    except Exception:
        pass
    
    try:
        c.execute("ALTER TABLE quiz_results_48 ADD COLUMN IF NOT EXISTS personality_report TEXT")
    except Exception:
        pass

# ============ 数据库类型检测 ============
def get_db_type():
    """返回当前使用的数据库类型"""
    return 'PostgreSQL' if USE_POSTGRES else 'SQLite'

def get_db_info():
    """返回数据库连接信息（用于调试）"""
    if USE_POSTGRES:
        return {
            'type': 'PostgreSQL',
            'url': 'postgresql://***:***@***'  # 隐藏敏感信息
        }
    else:
        return {
            'type': 'SQLite',
            'path': os.environ.get('DATABASE', 'quiz_results.db')
        }
