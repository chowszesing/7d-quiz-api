# Railway 部署指南 - 8维能力测评 API

## 🚀 快速部署步骤

### 步骤 1：连接 GitHub 仓库

1. 访问 [railway.app](https://railway.app)
2. 点击 **New Project** → **Deploy from GitHub repo**
3. 选择你的仓库 `chowszesing/7d-quiz-api`

### 步骤 2：添加 PostgreSQL 数据库

1. 在 Railway 项目页面，点击 **New** → **Database** → **Add PostgreSQL**
2. Railway 会自动创建数据库并设置 `DATABASE_URL` 环境变量
3. 等待数据库创建完成（通常 10-30 秒）

### 步骤 3：配置环境变量

Railway 会自动设置以下变量：
- `DATABASE_URL` - PostgreSQL 连接字符串（自动）
- `PORT` - 端口（自动）

你需要手动添加：
- `SECRET_KEY` - Flask 密钥（可选，用于安全目的）

### 步骤 4：部署

1. Railway 会自动检测 `requirements.txt` 并安装依赖
2. 自动运行 `gunicorn quiz_api_server:app`
3. 部署成功后，你会获得一个 URL：`https://7d-quiz-api.up.railway.app`

---

## 📋 手动配置（如果需要）

### 启动命令
```
gunicorn quiz_api_server:app -b 0.0.0.0:$PORT -w 1 --timeout 120
```

### 环境变量
| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DATABASE_URL` | PostgreSQL 连接字符串 | 自动设置 |
| `PORT` | HTTP 端口 | 8080 |
| `SECRET_KEY` | Flask 密钥 | auto-generated |

### Python 版本
使用 Python 3.11

---

## 🔧 本地开发

### SQLite 模式（默认）
```bash
cd c:\Users\85255\Downloads
python quiz_api_server.py
```
- 使用本地 SQLite 数据库
- 访问 `http://localhost:5000`

### PostgreSQL 模式（模拟 Railway）
```bash
# 设置 DATABASE_URL 环境变量
set DATABASE_URL=postgresql://user:password@localhost:5432/quiz_db

# 启动
python quiz_api_server.py
```

---

## 🗄️ 数据库结构

### 表 1：quiz_results（7题版）
```sql
CREATE TABLE quiz_results (
    id SERIAL PRIMARY KEY,
    user_name TEXT,
    industry TEXT,
    experience TEXT,
    answers TEXT,        -- JSON 字符串
    question_order TEXT, -- JSON 字符串
    scores TEXT,         -- JSON 字符串
    validity_check INTEGER,
    submitted_at TEXT,
    ip_address TEXT,
    user_agent TEXT
);
```

### 表 2：quiz_results_48（48题版）
```sql
CREATE TABLE quiz_results_48 (
    id SERIAL PRIMARY KEY,
    user_name TEXT,
    experience TEXT,
    industry TEXT,
    answers TEXT,
    question_order TEXT,
    scores TEXT,
    submitted_at TEXT,
    ip_address TEXT,
    user_agent TEXT,
    access_token TEXT,
    personality_report TEXT
);
```

### 表 3：access_tokens（Token 白名单）
```sql
CREATE TABLE access_tokens (
    token VARCHAR(64) PRIMARY KEY,
    used BOOLEAN DEFAULT FALSE,
    assigned_to TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    used_at TIMESTAMP
);
```

---

## 🔍 调试命令

### 查看数据库连接
```bash
curl https://your-app.railway.app/api/admin/init_db
```

### 查看 Token 列表
```bash
curl https://your-app.railway.app/api/token/list
```

---

## 🌐 自定义域名（可选）

### Hobby Plan ($5/月) 支持

1. 在 Railway 项目 → Settings → Domains
2. 添加你的域名
3. 在域名服务商添加 DNS 记录：
   - CNAME → `cname.railwaydns.net`

---

## 💰 成本说明

| 方案 | 价格 | 说明 |
|------|------|------|
| Hobby Plan | $5/月 | 包含 Web + PostgreSQL(1GB) |
| Free Trial | $5/30天 | 试用后需付费或服务暂停 |

---

## ❓ 常见问题

### Q: PostgreSQL 连接失败？
检查 `DATABASE_URL` 环境变量是否正确设置。

### Q: 数据库表未创建？
访问 `/api/admin/init_db` 手动触发初始化。

### Q: 中文字体显示异常？
Railway 容器内无中文字体，代码会自动下载 Noto Sans SC。

### Q: 服务休眠？
Railway Hobby Plan 不会休眠，但额度用完会暂停。
