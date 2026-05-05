#!/usr/bin/env python3
"""Deploy 7D Quiz to GitHub + Render"""
import os
import sys
from github import Github, GithubException

# 需要GitHub Personal Access Token
# 获取：https://github.com/settings/tokens (需要repo权限)
TOKEN = input("请输入GitHub PAT (https://github.com/settings/tokens): ").strip()

if not TOKEN:
    print("❌ 需要GitHub Token")
    sys.exit(1)

g = Github(TOKEN)
user = g.get_user()
print(f"✅ 已登录: {user.login}")

# 创建仓库
REPO_NAME = "7d-quiz-api"
try:
    repo = user.create_repo(
        name=REPO_NAME,
        description="7维能力测评 - Santa Chow",
        private=False,
        auto_init=True
    )
    print(f"✅ 仓库已创建: {repo.html_url}")
except GithubException as e:
    if e.status == 422:  # 已存在
        repo = user.get_repo(REPO_NAME)
        print(f"⚠️ 仓库已存在: {repo.html_url}")
    else:
        print(f"❌ 创建失败: {e}")
        sys.exit(1)

# 初始化git并推送
import subprocess

os.chdir("/c/Users/85255/Downloads")

# 初始化git
subprocess.run(["git", "init"], check=False)
subprocess.run(["git", "branch", "-M", "main"], check=False)

# 添加remote
try:
    subprocess.run(["git", "remote", "remove", "origin"], check=False)
    subprocess.run(["git", "remote", "add", "origin", repo.clone_url], check=True)
except:
    pass

# 创建.gitignore
with open(".gitignore", "w") as f:
    f.write("__pycache__/\n*.pyc\n*.db\n.env\n")

# 提交并推送
subprocess.run(["git", "add", "."], check=True)
subprocess.run(["git", "commit", "-m", "7维能力测评 - 初始版本"], check=False)
subprocess.run(["git", "push", "-u", "origin", "main", "--force"], check=True)

print(f"""
🎉 部署完成！

📦 仓库: {repo.html_url}
🚀 下一步:
   1. 访问 https://render.com
   2. 点击 "New +" → "Blueprint"
   3. 连接 GitHub 仓库: {REPO_NAME}
   4. 点击 "Apply" 开始部署

⏱️ 部署时间: 约5-10分钟
🌐 部署完成后访问: https://7d-quiz-api.onrender.com
""")
