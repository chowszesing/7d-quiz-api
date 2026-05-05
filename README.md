# 🎯 7維能力測評 - Render部署指南

## 快速部署（5分钟）

### 1. 创建GitHub仓库
```bash
git init
git add .
git commit -m "Initial commit"
gh repo create 7d-quiz-api --public --push
```

### 2. 连接Render
1. 访问 https://render.com
2. Login → New → Blueprint
3. 连接GitHub仓库
4. Render自动读取 `render.yaml` → 点击 Apply

### 3. 完成！
- 访问：`https://7d-quiz-api.onrender.com`
- 管理后台：`https://7d-quiz-api.onrender.com/admin`

---

## ⚠️ 注意事项

### 免费版限制
- 15分钟无活动会休眠（首次访问慢3-5秒）
- 建议搭配 UptimeRobot 定时ping
- SQLite数据库（休眠可能丢数据，测试阶段够用）

### 防止休眠
1. 注册 https://uptimerobot.com（免费）
2. 添加监控 → URL填你的服务地址
3. 间隔设为5分钟

---

## 📁 文件说明

| 文件 | 说明 |
|------|------|
| `quiz_api_server.py` | 主服务（Flask后端+HTML前端） |
| `requirements.txt` | Python依赖 |
| `render.yaml` | Render部署配置 |
| `quiz_results.db` | SQLite数据库（自动创建） |

---

## 🔧 本地测试

```bash
pip install -r requirements.txt
python quiz_api_server.py
# 访问 http://localhost:5000
```

---

## 📊 功能

- ✅ 在线问卷（28题随机顺序）
- ✅ 即时7维评分
- ✅ PDF报告下载
- ✅ 群体常模对比
- ✅ 管理后台（/admin）
- ✅ CSV数据导出
- ✅ 批量导入（离线问卷）
