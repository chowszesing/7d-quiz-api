"""
7维能力测评 - 一体化后端（Flask）
功能：问卷服务 + API + PDF报告 + 批量导入
部署：Railway
"""

from flask import Flask, request, jsonify, send_file, render_template_string
from werkzeug.security import generate_password_hash, check_password_hash
import jwt
from flask_cors import CORS
import json
import sqlite3
from functools import wraps
import os
import io
import csv
import urllib.request
from datetime import datetime
from contextlib import contextmanager

# PDF生成
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

# ============ V3 设计配色方案 ============
COLOR_PRIMARY = colors.HexColor('#1e3a8a')      # 深蓝 - 主色
COLOR_SECONDARY = colors.HexColor('#3b82f6')    # 中蓝 - 辅色
COLOR_ACCENT_GOOD = colors.HexColor('#10b981')   # 绿色 - 优势
COLOR_ACCENT_WARN = colors.HexColor('#f59e0b')   # 琥珀色 - 发展
COLOR_BG_LIGHT = colors.HexColor('#f8fafc')      # 浅灰 - 背景
COLOR_BG_CARD = colors.HexColor('#ffffff')         # 白色 - 卡片
COLOR_TEXT_DARK = colors.HexColor('#1e293b')      # 深灰 - 主文本
COLOR_TEXT_MID = colors.HexColor('#64748b')       # 中灰 - 次要文本
COLOR_BORDER = colors.HexColor('#e2e8f0')         # 边框色

app = Flask(__name__)
CORS(app)

# ============ 静态文件路由（report_engine.js 等前端资源）============
@app.route('/report_engine_data.js')
def serve_report_engine_data():
    """提供 report_engine_data.js"""
    from flask import send_from_directory
    import os
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'report_engine_data.js')

@app.route('/report_engine.js')
def serve_report_engine():
    """提供 report_engine.js"""
    from flask import send_from_directory
    import os
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'report_engine.js')

# 配置
DATABASE = os.environ.get('DATABASE', 'quiz_results.db')

# ---------- Admin 用户表与初始化 ----------
def init_admin_table():
    with get_db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS admin_user (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT CHECK(role IN ('admin','token_user')) NOT NULL DEFAULT 'admin'
            );
        ''')
        conn.commit()

def create_default_admin():
    # Railway 部署初始管理员账号
    username = 'admin'
    raw_pwd = 'Css2504stc1128Abc'
    pwd_hash = generate_password_hash(raw_pwd)
    with get_db() as conn:
        cur = conn.execute('SELECT id FROM admin_user WHERE username = ?', (username,))
        if not cur.fetchone():
            conn.execute('INSERT INTO admin_user (username, password_hash, role) VALUES (?,?,?)',
                         (username, pwd_hash, 'admin'))
            conn.commit()

init_admin_table()
create_default_admin()
PORT = int(os.environ.get('PORT', 5000))

# ============ 中文字体下载（Render 容器内无字体时使用）============
FONT_DOWNLOAD_DIR = '/tmp'  # Render 容器 /tmp 可写

FONT_CDN_SOURCES = [
    # Noto Sans SC 简体中文子集（推荐，最小 ~1.5MB）
    'https://github.com/googlefonts/noto-cjk/raw/main/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf',
    # Noto Sans SC TTF 版本
    'https://github.com/notofonts/noto-cjk/releases/download/Sans2.004/07_NotoSansSC.zip',
]

FONT_LOCAL_PATHS = [
    # 项目 fonts/ 目录（包含在 Git 仓库中，推荐方式）
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts', 'NotoSansSC-Regular.otf'),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts', 'NotoSansCJK-Regular.otf'),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts', 'NotoSansCJKsc-Regular.otf'),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts', 'wqy-microhei.ttc'),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts', 'NotoSansCJK-Regular.ttc'),
    # Windows 系统字体
    'C:/Windows/Fonts/msyh.ttc',   # 微软雅黑
    'C:/Windows/Fonts/simhei.ttf',  # 黑体
    'C:/Windows/Fonts/simsun.ttc', # 宋体
]

FONT_SYSTEM_EXPLICIT = [
    ('NotoSansCJKSC', '/usr/share/fonts/opentype/noto-cjk/NotoSansCJKsc-Regular.otf'),
    ('NotoSansCJKSC', '/usr/share/fonts/opentype/noto/NotoSansSC-Regular.otf'),
    ('WenQuanYiMicrohei', '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc'),
    ('WenQuanYiMicrohei', '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc'),
    ('DroidSansFallback', '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf'),
    ('NotoSansHant', '/usr/share/fonts/opentype/noto/NotoSansHant-Regular.otf'),
    ('SimHei', '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc'),
]

FONT_FILENAME = 'NotoSansSC-Regular.otf'

def download_chinese_font():
    """从 CDN 下载中文字体到 /tmp，返回字体路径，失败返回 None"""
    import urllib.request
    import zipfile

    target_path = os.path.join(FONT_DOWNLOAD_DIR, FONT_FILENAME)
    if os.path.exists(target_path):
        print(f"  [字体] 使用已下载字体: {target_path}")
        return target_path

    print(f"  [字体] 尝试下载 Noto Sans SC 字体到 {target_path}...")

    # 方案1：直接下载 OTF
    otf_url = 'https://github.com/googlefonts/noto-cjk/raw/main/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf'
    try:
        req = urllib.request.Request(otf_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        with open(target_path, 'wb') as f:
            f.write(data)
        print(f"  [字体] 下载成功: {len(data)} bytes -> {target_path}")
        return target_path
    except Exception as e:
        print(f"  [字体] OTF 下载失败: {e}")

    # 方案2：下载 zip 并解压
    zip_url = 'https://github.com/notofonts/noto-cjk/releases/download/Sans2.004/07_NotoSansSC.zip'
    try:
        req = urllib.request.Request(zip_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        zip_path = os.path.join(FONT_DOWNLOAD_DIR, 'NotoSansSC.zip')
        with open(zip_path, 'wb') as f:
            f.write(data)
        print(f"  [字体] ZIP 下载成功: {len(data)} bytes")
        # 解压
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                for name in zf.namelist():
                    if 'NotoSansSC-Regular.otf' in name or 'NotoSansSC-Regular.ttc' in name:
                        zf.extract(name, FONT_DOWNLOAD_DIR)
                        extracted = os.path.join(FONT_DOWNLOAD_DIR, name)
                        # 重命名到标准名
                        import shutil
                        shutil.move(extracted, target_path)
                        print(f"  [字体] 解压成功: {name} -> {target_path}")
                        return target_path
        except Exception as e2:
            print(f"  [字体] 解压失败: {e2}")
        os.remove(zip_path)
    except Exception as e:
        print(f"  [字体] ZIP 下载失败: {e}")

    print(f"  [字体] 所有下载方案均失败")
    return None

# ============ 中文字体注册 ============
def register_fonts():
    """注册中文字体，支持PDF中文输出；返回可用字体名称，失败返回None"""

    app_dir = os.path.dirname(os.path.abspath(__file__))

    # 候选字体来源
    candidates = []

    # 1. 本地字体文件（开发者机器）
    for path in FONT_LOCAL_PATHS:
        if os.path.exists(path):
            name = os.path.splitext(os.path.basename(path))[0].replace('-', '').replace('_', '').replace(' ', '')
            candidates.append((name, path))
            print(f"  [字体] 找到本地字体: {path}")

    # 2. 系统显式路径
    for name, path in FONT_SYSTEM_EXPLICIT:
        if os.path.exists(path) and not any(f == path for _, f in candidates):
            candidates.append((name, path))
            print(f"  [字体] 找到系统字体: {path}")

    # 3. 使用 fc-list 探测（Render 等 Linux 环境，最可靠）
    try:
        import subprocess
        result = subprocess.run(['fc-list', ':lang=zh', '-f', '%{file}\n'],
                               capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                if line and os.path.exists(line) and not any(p == line for _, p in candidates):
                    name = 'NotoSansSC'
                    candidates.append((name, line))
                    print(f"  [字体] fc-list 发现中文字体: {line}")
    except Exception as e:
        print(f"  [字体] fc-list 探测失败: {e}")

    # 4. 递归搜索系统字体目录（兜底）
    import glob
    system_font_dirs = [
        '/usr/share/fonts/', '/usr/local/share/fonts/',
        '/opt/fonts/', os.path.expanduser('~/.fonts/'),
    ]
    for font_dir in system_font_dirs:
        if not os.path.exists(font_dir):
            continue
        for ext in ['*.ttf', '*.otf', '*.ttc']:
            for f in glob.glob(os.path.join(font_dir, '**', ext), recursive=True):
                basename = os.path.basename(f).lower()
                skip_patterns = ['dejavu', 'liberation', 'ubuntu', 'freefont', 'glyphicons', 'fontawesome']
                if any(p in basename for p in skip_patterns):
                    continue
                cjk_patterns = ['cjk', 'noto', 'wqy', 'chinese', 'zh', 'sc', 'tc', 'hans', 'hant', 'droid', 'source']
                if any(p in basename for p in cjk_patterns):
                    name = os.path.splitext(os.path.basename(f))[0].replace('-', '').replace('_', '').replace(' ', '')
                    if not any(f == f for _, f in candidates):
                        candidates.append((name, f))
                        print(f"  [字体] 找到系统CJK字体: {f}")

    # 5. 尝试下载（Render 等容器环境，最后兜底）
    if not candidates:
        print(f"  [字体] 未找到任何字体，尝试下载...")
        downloaded = download_chinese_font()
        if downloaded:
            name = 'NotoSansSC'
            candidates.append((name, downloaded))

    # 去重
    seen_paths = set()
    unique_candidates = []
    for name, path in candidates:
        if path not in seen_paths:
            seen_paths.add(path)
            unique_candidates.append((name, path))
    candidates = unique_candidates

    print(f"\n{'='*50}")
    print(f"开始字体注册，共 {len(candidates)} 个候选")
    print(f"{'='*50}")

    for name, path in candidates:
        try:
            # 注册 Regular 版本
            font_regular = TTFont(name, path)
            pdfmetrics.registerFont(font_regular)
            print(f"✓ 成功注册 Regular: {name}")
            print(f"  路径: {path}")
            
            # 同时注册 Bold 版本（使用相同字形，中文字体无独立Bold变体）
            font_bold_name = name + '-Bold'
            font_bold = TTFont(font_bold_name, path)
            pdfmetrics.registerFont(font_bold)
            print(f"✓ 成功注册 Bold: {font_bold_name}")
            
            print(f"{'='*50}\n")
            return name
        except Exception as e:
            print(f"  ✗ 失败: {name} ({path}): {e}")
            continue

    print(f"⚠️ 警告: 未找到中文字体，PDF中文将显示异常")
    print(f"{'='*50}\n")
    return None

CHINESE_FONT = register_fonts()
print(f"字体注册完成: CHINESE_FONT = {CHINESE_FONT}")

# ============ HTML模板（简体中文）============
HTML_INDEX = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>7维能力测评 | Santa Chow</title>
    <style>
        *{box-sizing:border-box;margin:0;padding:0}
        body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#f5f7fa;color:#333}
        .container{max-width:700px;margin:0 auto;padding:20px}
        .header{text-align:center;padding:30px 0;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:white;border-radius:0 0 20px 20px;margin-bottom:30px}
        .header h1{font-size:28px;margin-bottom:10px}
        .header p{opacity:0.9;font-size:14px}
        .section{background:white;border-radius:12px;padding:25px;margin-bottom:20px;box-shadow:0 2px 10px rgba(0,0,0,0.05)}
        .section-title{font-size:18px;font-weight:600;margin-bottom:20px;color:#667eea;border-bottom:2px solid #667eea;padding-bottom:10px}
        .form-group{margin-bottom:15px}
        .form-group label{display:block;margin-bottom:5px;font-weight:500}
        .form-group input,.form-group select{width:100%;padding:12px;border:1px solid #ddd;border-radius:8px;font-size:14px}
        .form-group input:focus,.form-group select:focus{outline:none;border-color:#667eea}
        .question{background:#f8f9fc;border-radius:10px;padding:20px;margin-bottom:15px}
        .question-text{font-weight:500;margin-bottom:15px;line-height:1.6}
        .question-meta{font-size:12px;color:#888;margin-bottom:10px}
        .options{display:flex;gap:10px;flex-wrap:wrap}
        .option{flex:1;min-width:80px}
        .option input{display:none}
        .option label{display:block;text-align:center;padding:10px 5px;background:white;border:2px solid #ddd;border-radius:8px;cursor:pointer;transition:all 0.2s;font-size:13px}
        .option input:checked+label{background:#667eea;color:white;border-color:#667eea}
        .option label:hover{border-color:#667eea}
        .progress-bar{background:#e0e0e0;height:8px;border-radius:4px;margin-bottom:20px;overflow:hidden}
        .progress-fill{background:linear-gradient(90deg,#667eea,#764ba2);height:100%;transition:width 0.3s;width:0%}
        .btn{display:inline-block;padding:14px 30px;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:white;border:none;border-radius:8px;font-size:16px;font-weight:600;cursor:pointer;transition:transform 0.2s}
        .btn:hover{transform:translateY(-2px)}
        .btn-secondary{background:white;color:#667eea;border:2px solid #667eea}
        .result-card{background:white;border-radius:16px;padding:30px;text-align:center}
        .score-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:15px;margin:20px 0}
        .score-item{background:#f8f9fc;padding:15px;border-radius:10px;text-align:center}
        .score-label{font-size:12px;color:#888;margin-bottom:5px}
        .score-value{font-size:24px;font-weight:700;color:#667eea}
        .score-level{font-size:11px;color:#666;margin-top:3px}
        .hidden{display:none}
        .nav-buttons{display:flex;justify-content:space-between;margin-top:20px}
        .stats-panel{background:#f8f9fc;padding:15px;border-radius:10px;margin-top:20px}
        .stats-row{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #eee}
        .admin-link{position:fixed;bottom:20px;right:20px;background:rgba(0,0,0,0.7);color:white;padding:8px 15px;border-radius:20px;font-size:12px;text-decoration:none}
        .footer{text-align:center;padding:20px;font-size:12px;color:#888;border-top:1px solid #eee;margin-top:30px}
    </style>
</head>
<body>
    <div class="header">
        <h1>🎯 7维能力测评</h1>
        <p>由 Santa Chow 专业教练提供 | 约5分钟完成</p>
    </div>
    <div class="container">
        <div class="section" id="info-section">
            <div class="section-title">👤 基本信息</div>
            <div class="form-group"><label>姓名 / Name</label><input type="text" id="userName" placeholder="选填"></div>
            <div class="form-group">
                <label>目标行业 *</label>
                <select id="industry" required>
                    <option value="">请选择...</option>
                    <option value="银行/金融">银行/金融</option><option value="投资银行">投资银行</option>
                    <option value="四大审计">四大审计</option><option value="管理咨询">管理咨询</option>
                    <option value="科技/互联网">科技/互联网</option><option value="保险">保险</option>
                    <option value="房地产">房地产</option><option value="零售/快消">零售/快消</option>
                    <option value="政府/公共事业">政府/公共事业</option><option value="其他">其他</option>
                </select>
            </div>
            <div class="form-group">
                <label>工作年限 *</label>
                <select id="experience" required>
                    <option value="">请选择...</option>
                    <option value="应届生">应届生</option><option value="1-3年">1-3年</option>
                    <option value="3-5年">3-5年</option><option value="5-10年">5-10年</option>
                    <option value="10年以上">10年以上</option>
                </select>
            </div>
            <button class="btn" onclick="startQuiz()" style="width:100%">开始测评 →</button>
        </div>
        <div class="section hidden" id="quiz-section">
            <div class="progress-bar"><div class="progress-fill" id="progress"></div></div>
            <div id="question-container"></div>
            <div class="nav-buttons">
                <button class="btn btn-secondary" id="prevBtn" onclick="prevQuestion()">← 上一题</button>
                <button class="btn" id="nextBtn" onclick="nextQuestion()">下一题 →</button>
            </div>
        </div>
        <div class="section hidden" id="result-section">
            <div class="result-card">
                <h2 style="margin-bottom:10px">✨ 测评完成！</h2>
                <p style="color:#888;margin-bottom:20px" id="result-name"></p>
                <div id="scores-display"></div>
                <div style="margin-top:20px">
                    <button class="btn" onclick="downloadReport()">📄 下载PDF报告</button>
                    <button class="btn btn-secondary" onclick="resetQuiz()" style="margin-left:10px">重新测评</button>
                </div>
            </div>
            <div class="stats-panel"><h4 style="margin-bottom:10px">📊 群体对比</h4><div id="norm-comparison"></div></div>
        </div>
    </div>
    <div class="footer">
        <p>© 2026 Santa Chow 专业教练 | 7维能力测评系统</p>
        <p>如有疑问请联系：Santa Chow</p>
    </div>
    <a href="/admin" class="admin-link">⚙️ 管理后台</a>
    <script>
        const API='';
        const DIM_NAMES={COG:'思维敏锐度',TEC:'数字应用力',COM:'沟通穿透力',SOC:'人际连结力',ORG:'目标驱动力',PRS:'应变决策力',MGT:'团队赋能力'};
        let currentQ=0,questionOrder=[],answers={},resultId=null;

        const MAIN_COUNT=28;
        const VALIDITY_START=28;
        const TOTAL_QUESTIONS=31;

        const questions=[
            {id:1,text:'我能快速理解新事物的核心原理',dim:'COG'},
            {id:2,text:'面对复杂问题时，我能迅速找到关键脉络',dim:'COG'},
            {id:3,text:'我善于总结归纳，能把复杂信息简化',dim:'COG'},
            {id:4,text:'我对数据和逻辑敏感，能理性分析',dim:'COG'},
            {id:5,text:'我能熟练使用AI工具（ChatGPT、Claude、Midjourney等）提升工作效率',dim:'TEC'},
            {id:6,text:'遇到技术问题时，我能快速排查原因',dim:'TEC'},
            {id:7,text:'我会主动学习新技术保持竞争力',dim:'TEC'},
            {id:8,text:'我能把复杂技术概念解释给非专业人士',dim:'TEC'},
            {id:9,text:'我能清晰表达复杂的想法',dim:'COM'},
            {id:10,text:'我善于倾听，能理解对方的真实需求',dim:'COM'},
            {id:11,text:'书面表达（邮件、报告）逻辑清晰',dim:'COM'},
            {id:12,text:'演讲或简报时，我能吸引听众注意力',dim:'COM'},
            {id:13,text:'我容易与不同背景的人建立信任',dim:'SOC'},
            {id:14,text:'我能敏锐察觉他人的情绪变化',dim:'SOC'},
            {id:15,text:'团队冲突时，我能调和各方立场',dim:'SOC'},
            {id:16,text:'我善于拓展和维护人际网络',dim:'SOC'},
            {id:17,text:'我能设定清晰可衡量的目标',dim:'ORG'},
            {id:18,text:'我按计划执行，很少拖延',dim:'ORG'},
            {id:19,text:'我善于合理分配时间和资源',dim:'ORG'},
            {id:20,text:'我会定期回顾和优化工作流程',dim:'ORG'},
            {id:21,text:'压力下我仍能保持冷静和理性',dim:'PRS'},
            {id:22,text:'面对突发情况，我能快速调整策略',dim:'PRS'},
            {id:23,text:'我倾向于分析问题根本原因而非表面',dim:'PRS'},
            {id:24,text:'做决策时，我能权衡利弊后果断行动',dim:'PRS'},
            {id:25,text:'我会赋权给团队成员，信任他们的判断',dim:'MGT'},
            {id:26,text:'我能有效协调跨部门合作',dim:'MGT'},
            {id:27,text:'我会及时提供反馈，帮助他人成长',dim:'MGT'},
            {id:28,text:'团队士气低落时，我能激励团队',dim:'MGT'},
            {id:29,text:'总体而言，我认为本测评能准确反映我的能力水平',dim:'V'},
            {id:30,text:'本测评的题目表述清晰易懂，我能准确理解每道题的意思',dim:'V'},
            {id:31,text:'我愿意向朋友或同事推荐本测评工具',dim:'V'}
        ];

        const opts=['非常不同意','不同意','普通','同意','非常同意'];

        function shuffleQuestions(){
            const mainIndices=[...Array(MAIN_COUNT).keys()];
            for(let i=mainIndices.length-1;i>0;i--){
                const j=Math.floor(Math.random()*(i+1));
                [mainIndices[i],mainIndices[j]]=[mainIndices[j],mainIndices[i]];
            }
            questionOrder=[...mainIndices, VALIDITY_START, VALIDITY_START+1, VALIDITY_START+2];
        }

        function startQuiz(){
            const industry=document.getElementById('industry').value;
            const experience=document.getElementById('experience').value;
            if(!industry||!experience){alert('请填写必填项');return}
            sessionStorage.setItem('industry',industry);
            sessionStorage.setItem('experience',experience);
            sessionStorage.setItem('userName',document.getElementById('userName').value);
            shuffleQuestions();
            currentQ=0;
            answers={};
            document.getElementById('info-section').classList.add('hidden');
            document.getElementById('quiz-section').classList.remove('hidden');
            renderQuestion();
        }

        function renderQuestion(){
            const qIdx=questionOrder[currentQ];
            const q=questions[qIdx];
            const isLast=(currentQ===TOTAL_QUESTIONS-1);
            const isValidity=(q.dim==='V');

            document.getElementById('progress').style.width=((currentQ+1)/TOTAL_QUESTIONS*100)+'%';
            document.getElementById('prevBtn').style.visibility=currentQ>0?'visible':'hidden';

            let btnText;
            if(isValidity){
                btnText=isLast?'提交测评 ✓':'下一题 →';
            }else{
                btnText=(currentQ<MAIN_COUNT-1)?'下一题 →':'下一题 →';
            }
            if(isLast) btnText='提交测评 ✓';
            document.getElementById('nextBtn').textContent=btnText;

            document.getElementById('question-container').innerHTML=`
                <div class="question">
                    <div class="question-meta">第 ${currentQ+1} / ${TOTAL_QUESTIONS} 题 | ${isValidity?'问卷反馈':DIM_NAMES[q.dim]}</div>
                    <div class="question-text">${q.text}</div>
                    <div class="options">${opts.map((o,i)=>`<div class="option"><input type="radio" name="answer" id="opt${i}" value="${i+1}" ${answers[q.id]==i+1?'checked':''}><label for="opt${i}">${o}</label></div>`).join('')}</div>
                </div>`;
        }

        function nextQuestion(){
            const selected=document.querySelector('input[name="answer"]:checked');
            if(!selected){alert('请选择一个选项');return}
            const qIdx=questionOrder[currentQ];
            answers[questions[qIdx].id]=parseInt(selected.value);
            if(currentQ<TOTAL_QUESTIONS-1){
                currentQ++;
                renderQuestion();
            }else{
                submitQuiz();
            }
        }

        function prevQuestion(){if(currentQ>0){currentQ--;renderQuestion()}}

        async function submitQuiz(){
            const industry=sessionStorage.getItem('industry');
            const experience=sessionStorage.getItem('experience');
            const userName=sessionStorage.getItem('userName')||'匿名用户';
            try{
                const res=await fetch(API+'/api/quiz/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:userName,industry,experience,answers,question_order:questionOrder})});
                const data=await res.json();
                resultId=data.result_id;
                document.getElementById('quiz-section').classList.add('hidden');
                document.getElementById('result-section').classList.remove('hidden');
                document.getElementById('result-name').textContent=userName+' | '+industry+' | '+experience;
                let html='<div class="score-grid">';
                Object.entries(data.scores).forEach(([dim,s])=>{html+=`<div class="score-item"><div class="score-label">${s.name}</div><div class="score-value">${s.average.toFixed(1)}</div><div class="score-level">${s.level}</div></div>`});
                html+='</div>';
                document.getElementById('scores-display').innerHTML=html;
            }catch(e){
                console.error('API调用失败，使用本地计算:', e);
                calculateAndShowScoresLocal(industry, experience, userName);
            }
        }

        function calculateAndShowScoresLocal(industry, experience, userName){
            document.getElementById('quiz-section').classList.add('hidden');
            document.getElementById('result-section').classList.remove('hidden');
            document.getElementById('result-name').textContent=userName+' | '+industry+' | '+experience;

            const dims = {COG:[1,2,3,4],TEC:[5,6,7,8],COM:[9,10,11,12],SOC:[13,14,15,16],ORG:[17,18,19,20],PRS:[21,22,23,24],MGT:[25,26,27,28]};
            const dimNames = {COG:'思维敏锐度',TEC:'数字应用力',COM:'沟通穿透力',SOC:'人际连结力',ORG:'目标驱动力',PRS:'应变决策力',MGT:'团队赋能力'};

            let html='<div class="score-grid">';
            for(const [dim,qids] of Object.entries(dims)){
                const avg= qids.reduce((sum,q)=>sum+(answers[q]||3),0)/4;
                const level=avg>=4.5?'优秀':avg>=3.5?'良好':avg>=2.5?'中等':avg>=1.5?'待提升':'需改进';
                html+=`<div class="score-item"><div class="score-label">${dimNames[dim]}</div><div class="score-value">${avg.toFixed(1)}</div><div class="score-level">${level}</div></div>`;
            }
            html+='</div>';
            document.getElementById('scores-display').innerHTML=html;
            alert('注意：结果已本地计算，服务器连接失败，PDF报告功能暂时不可用');
        }

        async function downloadReport(){if(resultId)window.open(API+'/api/quiz/report/'+resultId,'_blank')}
        function resetQuiz(){currentQ=0;answers={};resultId=null;document.getElementById('result-section').classList.add('hidden');document.getElementById('info-section').classList.remove('hidden')}
    </script>
</body>
</html>'''

HTML_ADMIN = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>管理后台 | 8维能力测评</title>
    <style>
        *{box-sizing:border-box;margin:0;padding:0}
        body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Microsoft YaHei",sans-serif;background:#f5f7fa;color:#333}
        .container{max-width:1300px;margin:0 auto;padding:20px}
        .header{background:linear-gradient(135deg,#1e3a8a 0%,#3b82f6 100%);color:white;padding:20px;border-radius:12px;margin-bottom:20px;display:flex;justify-content:space-between;align-items:center}
        .header h1{font-size:24px;margin-bottom:5px}
        .header-right{display:flex;align-items:center;gap:12px}
        .user-info{font-size:13px;opacity:0.9}
        .btn-logout{padding:7px 14px;background:rgba(255,255,255,0.2);color:white;border:1px solid rgba(255,255,255,0.4);border-radius:6px;cursor:pointer;font-size:13px}
        .btn-logout:hover{background:rgba(255,255,255,0.3)}
        .tabs{display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap}
        .tab{padding:10px 20px;background:white;border-radius:8px;cursor:pointer;font-size:14px;font-weight:500;border:2px solid transparent;transition:all 0.2s}
        .tab.active{background:#1e3a8a;color:white}
        .card{background:white;border-radius:12px;padding:20px;margin-bottom:20px;box-shadow:0 2px 10px rgba(0,0,0,0.05)}
        .card h2{font-size:16px;color:#1e3a8a;margin-bottom:15px;padding-bottom:10px;border-bottom:2px solid #1e3a8a}
        .stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:15px;margin-bottom:20px}
        .stat-box{background:#f8f9fc;padding:20px;border-radius:10px;text-align:center}
        .stat-value{font-size:32px;font-weight:700;color:#1e3a8a}
        .stat-label{font-size:12px;color:#888;margin-top:5px}
        table{width:100%;border-collapse:collapse}
        th,td{padding:10px 8px;text-align:left;border-bottom:1px solid #eee;font-size:13px}
        th{background:#f8f9fc;font-weight:600;color:#1e3a8a;white-space:nowrap}
        tr:hover{background:#f8f9fc}
        .btn{padding:8px 14px;background:#1e3a8a;color:white;border:none;border-radius:6px;cursor:pointer;font-size:13px}
        .btn:hover{background:#2563eb}
        .btn-sm{padding:5px 10px;font-size:12px}
        .btn-red{background:#ef4444}.btn-red:hover{background:#dc2626}
        .btn-green{background:#10b981}.btn-green:hover{background:#059669}
        .import-section{border:2px dashed #ddd;padding:30px;text-align:center;border-radius:12px;margin-top:20px}
        .msg{background:#f0f9ff;border-left:4px solid #1e3a8a;padding:12px 16px;border-radius:6px;margin:10px 0;font-size:13px}
        .hidden{display:none}
        /* 登录页 */
        #loginOverlay{position:fixed;inset:0;background:rgba(15,23,42,0.85);display:flex;align-items:center;justify-content:center;z-index:9999}
        .login-card{background:white;border-radius:16px;padding:40px;width:400px;max-width:90vw;text-align:center;box-shadow:0 25px 60px rgba(0,0,0,0.4)}
        .login-card h2{font-size:22px;color:#1e3a8a;margin-bottom:8px}
        .login-card p{font-size:13px;color:#888;margin-bottom:30px}
        .login-input{width:100%;padding:12px 14px;border:1px solid #e2e8f0;border-radius:8px;font-size:15px;margin-bottom:12px;outline:none;transition:border-color 0.2s}
        .login-input:focus{border-color:#3b82f6}
        .login-btn{width:100%;padding:12px;background:#1e3a8a;color:white;border:none;border-radius:8px;font-size:15px;cursor:pointer;font-weight:600}
        .login-btn:hover{background:#2563eb}
        .login-error{background:#fee2e2;color:#991b1b;border-radius:6px;padding:10px;margin-bottom:15px;font-size:13px;display:none}
    </style>
</head>
<body>

    <!-- 登录页 -->
    <div id="loginOverlay">
        <div class="login-card">
            <h2>🔐 管理后台登录</h2>
            <p>请输入管理员账号密码</p>
            <div id="loginError" class="login-error"></div>
            <input type="text" id="username" class="login-input" placeholder="用户名" autocomplete="username">
            <input type="password" id="password" class="login-input" placeholder="密码" autocomplete="current-password" onkeydown="if(event.key==='Enter')doLogin()">
            <button class="login-btn" onclick="doLogin()">登录</button>
        </div>
    </div>

    <!-- Admin 主面板 -->
    <div id="adminPanel" class="hidden">
        <div class="container">
            <div class="header">
                <div>
                    <h1>⚙️ 8维能力测评 - 管理后台</h1>
                    <p>数据管理 | 报告下载</p>
                </div>
                <div class="header-right">
                    <span class="user-info" id="userInfo"></span>
                    <button class="btn-logout" onclick="doLogout()">退出登录</button>
                </div>
            </div>
            <div class="tabs">
                <div class="tab active" onclick="showTab('records')">📋 48题记录</div>
                <div class="tab" onclick="showTab('import')">📤 批量导入</div>
            </div>

            <!-- 48题记录 -->
            <div id="tab-records">
                <div class="stats-grid" id="stats48"></div>
                <div class="card">
                    <h2>📋 48题测评记录</h2>
                    <div style="margin-bottom:15px;display:flex;gap:10px;flex-wrap:wrap">
                        <input type="text" id="searchName" placeholder="搜索姓名..." style="padding:8px;border:1px solid #ddd;border-radius:6px;width:160px">
                        <select id="filterIndustry48" style="padding:8px;border:1px solid #ddd;border-radius:6px"><option value="">所有行业</option></select>
                        <button class="btn" onclick="load48()">搜索</button>
                    </div>
                    <div style="overflow-x:auto">
                        <table><thead><tr><th>ID</th><th>姓名</th><th>行业</th><th>年限</th><th>提交时间</th><th>操作</th></tr></thead><tbody id="table48"></tbody></table>
                    </div>
                </div>
            </div>

            <!-- 批量导入 -->
            <div id="tab-import" class="hidden">
                <div class="card">
                    <h2>📤 批量导入（离线问卷）</h2>
                    <div class="import-section">
                        <p>上传CSV文件批量导入测评结果</p>
                        <p style="font-size:12px;color:#888;margin:10px 0">格式：name, industry, experience, q1-q31（每题1-5分）</p>
                        <input type="file" id="csvFile" accept=".csv">
                        <button class="btn" onclick="importCSV()" style="margin-top:10px">导入</button>
                        <div id="importResult" style="margin-top:10px"></div>
                    </div>
                </div>
            </div>

            <div style="text-align:center;margin-top:20px"><a href="/" style="color:#1e3a8a">← 返回首页</a></div>
        </div>
    </div>

    <script>
        const API = '';
        var _token = localStorage.getItem('admin_token') || '';

        function authHeaders() {
            return _token ? {'Authorization': 'Bearer ' + _token} : {};
        }

        async function checkSession() {
            if (!_token) { showLogin(); return; }
            const res = await fetch(API + '/api/admin/check', {headers: authHeaders()});
            if (res.ok) {
                const data = await res.json();
                showAdmin(data.username || 'admin');
            } else {
                localStorage.removeItem('admin_token');
                _token = '';
                showLogin();
            }
        }

        async function doLogin() {
            const username = document.getElementById('username').value.trim();
            const password = document.getElementById('password').value;
            const errEl = document.getElementById('loginError');
            errEl.style.display = 'none';
            if (!username || !password) {
                errEl.textContent = '请输入用户名和密码';
                errEl.style.display = 'block';
                return;
            }
            try {
                const res = await fetch(API + '/admin/login', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({username, password})
                });
                const data = await res.json();
                if (data.token) {
                    _token = data.token;
                    localStorage.setItem('admin_token', _token);
                    showAdmin(data.username || username);
                } else {
                    errEl.textContent = data.msg || data.error || '登录失败，请检查账号密码';
                    errEl.style.display = 'block';
                }
            } catch(e) {
                errEl.textContent = '网络错误，请稍后重试';
                errEl.style.display = 'block';
            }
        }

        function doLogout() {
            _token = '';
            localStorage.removeItem('admin_token');
            showLogin();
        }

        function showLogin() {
            document.getElementById('loginOverlay').classList.remove('hidden');
            document.getElementById('adminPanel').classList.add('hidden');
        }

        function showAdmin(username) {
            document.getElementById('loginOverlay').classList.add('hidden');
            document.getElementById('adminPanel').classList.remove('hidden');
            document.getElementById('userInfo').textContent = '👤 ' + username;
            load48();
        }

        function showTab(name) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('[id^=tab-]').forEach(t => t.classList.add('hidden'));
            document.getElementById('tab-' + name).classList.remove('hidden');
            if (event) event.target.classList.add('active');
        }

        async function load48() {
            const name = document.getElementById('searchName').value;
            const res = await fetch(API + '/api/quiz/list_48?limit=200', {headers: authHeaders()});
            if (res.status === 401 || res.status === 403) { doLogout(); return; }
            const data = await res.json();
            document.getElementById('stats48').innerHTML = '<div class="stat-box"><div class="stat-value">' + data.count + '</div><div class="stat-label">48题总记录</div></div>';
            const industries = [...new Set((data.results || []).map(r => r.industry || ''))].filter(Boolean);
            document.getElementById('filterIndustry48').innerHTML = '<option value="">所有行业</option>' +
                industries.map(i => '<option value="' + i + '">' + i + '</option>').join('');
            const filtered = (data.results || []).filter(r => !name || (r.user_name || '').includes(name));
            document.getElementById('table48').innerHTML = filtered.map(r => '<tr><td>' + r.id + '</td><td>' + (r.user_name || '匿名') + '</td><td>' + (r.industry || '-') + '</td><td>' + (r.experience || '-') + '</td><td>' + (r.submitted_at ? new Date(r.submitted_at).toLocaleDateString() : '-') + '</td><td><button class="btn btn-sm" onclick="window.open(\\'' + API + '/api/quiz/report_48/' + r.id + '\\',\\'_blank\\')">PDF</button></td></tr>').join('') || '<tr><td colspan="6" style="text-align:center;color:#888">暂无记录</td></tr>';
        }

        async function importCSV() {
            const file = document.getElementById('csvFile').files[0];
            if (!file) { alert('请选择CSV文件'); return; }
            const formData = new FormData();
            formData.append('file', file);
            try {
                const res = await fetch(API + '/api/quiz/batch-import', {method: 'POST', body: formData});
                const data = await res.json();
                document.getElementById('importResult').innerHTML = '<b style="color:' + (data.success ? '#065f46' : '#991b1b') + '">' + (data.message || '') + '</b> 成功: ' + (data.success_count || 0) + ' 失败: ' + (data.fail_count || 0);
            } catch(e) { document.getElementById('importResult').innerHTML = '<b style="color:#991b1b">导入失败: ' + e.message + '</b>'; }
        }

        checkSession();
    </script>
</body>
</html>'''

# ============ 数据库函数（支持 SQLite 和 PostgreSQL）============
# Railway/PostgreSQL 环境使用 DATABASE_URL 环境变量
# 本地开发使用 DATABASE 环境变量指定的 SQLite 文件

# 导入数据库适配器
try:
    from db_adapter import get_db, init_db, USE_POSTGRES, json_encode, json_decode, execute_with_pk, get_db_type, get_db_info
    print(f"[DB] 使用数据库类型: {get_db_type()}")
except ImportError:
    # 降级：使用原生 SQLite
    USE_POSTGRES = False
    @contextmanager
    def get_db():
        conn = sqlite3.connect(DATABASE)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    def init_db():
        with get_db() as conn:
            c = conn.cursor()
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
                access_token TEXT)''')
            c.execute('''CREATE TABLE IF NOT EXISTS access_tokens (
                token TEXT PRIMARY KEY,
                used INTEGER DEFAULT 0,
                assigned_to TEXT,
                created_at TEXT,
                used_at TEXT)''')
            try:
                c.execute("ALTER TABLE quiz_results_48 ADD COLUMN access_token TEXT")
            except sqlite3.OperationalError:
                pass
            conn.commit()
    print("[DB] 警告：db_adapter.py 未找到，使用原生 SQLite")


# ============ Token 持久化：GitHub Gist 备份/恢复 ============
# Render 容器文件系统非持久化，每次重新部署都会清空数据库。
def calculate_scores(answers):
    dims = {'COG':{'name':'思维敏锐度','q':[1,2,3,4]},
            'TEC':{'name':'数字应用力','q':[5,6,7,8]},
            'COM':{'name':'沟通穿透力','q':[9,10,11,12]},
            'SOC':{'name':'人际连结力','q':[13,14,15,16]},
            'ORG':{'name':'目标驱动力','q':[17,18,19,20]},
            'PRS':{'name':'应变决策力','q':[21,22,23,24]},
            'MGT':{'name':'团队赋能力','q':[25,26,27,28]}}
    scores = {}
    for dim, cfg in dims.items():
        total = sum(answers.get(f'q{q}', 0) for q in cfg['q'])
        avg = total / 4
        scores[dim] = {'name': cfg['name'], 'average': round(avg, 2), 'level': get_level(avg)}
    return scores

def calculate_scores_48(answers):
    """8维能力评分：每维6题，支持str/int keys，支持乱序题目"""
    # 题目ID→维度映射：(qid-1)//6
    DIM_ORDER = ['COG', 'TEC', 'COM', 'SOC', 'ORG', 'PRS', 'MGT', 'LLA']
    def qid_to_dim(qid):
        return DIM_ORDER[(int(qid) - 1) // 6]

    # Normalize keys to int
    normalized = {}
    for k, v in answers.items():
        try:
            normalized[int(k)] = int(v)
        except (ValueError, TypeError):
            continue
    # 按维度分组累加
    dim_sums = {d: 0 for d in DIM_ORDER}
    dim_cnts = {d: 0 for d in DIM_ORDER}
    for qid, score in normalized.items():
        dim = qid_to_dim(qid)
        dim_sums[dim] += score
        dim_cnts[dim] += 1
    DIM_CN = {
        'COG':'认知能力','TEC':'技术掌握','COM':'理解表达',
        'SOC':'社交技能','ORG':'策划执行','PRS':'解决问题',
        'MGT':'管理技能','LLA':'持续学习',
    }
    scores = {}
    for dim in DIM_ORDER:
        cnt = dim_cnts[dim]
        avg = dim_sums[dim] / cnt if cnt > 0 else 3.0
        scores[dim] = {'name': DIM_CN[dim], 'average': round(avg, 2), 'level': get_level(avg)}
    return scores


def calculate_sub_scores_48(answers):
    """
    计算24项子能力分数：每维度3项子能力，每项2题
    子能力映射：
      - 维度内Q1-2 → 子能力1
      - 维度内Q3-4 → 子能力2
      - 维度内Q5-6 → 子能力3
    返回: [(dim, sub_idx, sub_name, score, level), ...] 按分数升序排列
    """
    normalized = {}
    for k, v in answers.items():
        try:
            normalized[int(k)] = int(v)
        except (ValueError, TypeError):
            continue

    # 子能力定义：(维度, 子能力索引0-2, 名称)
    sub_competencies = {
        # COG - 认知能力
        ('COG', 0): ('资讯提炼', '快速从复杂信息中提取关键重点'),
        ('COG', 1): ('逻辑推理', '理性分析矛盾资讯，发现论证漏洞'),
        ('COG', 2): ('快速学习', '短时间内掌握全新技术领域'),
        # TEC - 技术掌握
        ('TEC', 0): ('数字生产力', '有效运用AI工具和数据分析工具'),
        ('TEC', 1): ('技术适应力', '面对新技术能快速上手'),
        ('TEC', 2): ('故障排查', '能自主排查技术问题根本原因'),
        # COM - 理解表达
        ('COM', 0): ('解码能力', '准确理解对方言辞背后的真正意图'),
        ('COM', 1): ('精炼表达', '用简洁清晰的语言表达复杂概念'),
        ('COM', 2): ('口头影响力', '在公开发言中有效吸引听众注意力'),
        # SOC - 社交技能
        ('SOC', 0): ('情绪觉察', '敏锐感知他人情绪的细微变化'),
        ('SOC', 1): ('冲突协调', '在团队分歧中促进各方达成共识'),
        ('SOC', 2): ('关系建立', '与不同背景的人建立信任'),
        # ORG - 策划执行
        ('ORG', 0): ('目标规划', '将模糊目标拆解为清晰可衡量步骤'),
        ('ORG', 1): ('自主执行', '无外部监督时仍维持高标准'),
        ('ORG', 2): ('资源管理', '合理分配时间、人力、预算等资源'),
        # PRS - 解决问题
        ('PRS', 0): ('应变能力', '原方案失败时迅速产出替代方案'),
        ('PRS', 1): ('根源分析', '用结构化方法深挖问题根本原因'),
        ('PRS', 2): ('创新方案', '无既有SOP时自行设计有效解决方案'),
        # MGT - 管理技能
        ('MGT', 0): ('预期管理', '有效管理上下级对结果的期望'),
        ('MGT', 1): ('优先级取舍', '准确判断轻重缓急，敢于拒绝干扰'),
        ('MGT', 2): ('授权追踪', '有效分配任务并建立跟进机制'),
        # LLA - 持续学习
        ('LLA', 0): ('知识更新', '保持定期阅读行业书刊、参加课程'),
        ('LLA', 1): ('主动探索', '跨界探索本职以外的新领域'),
        ('LLA', 2): ('挫折转化', '将负面反馈转化为改进养分'),
    }

    # 维度基础题号
    dim_base = {
        'COG': 1, 'TEC': 7, 'COM': 13, 'SOC': 19,
        'ORG': 25, 'PRS': 31, 'MGT': 37, 'LLA': 43
    }

    results = []
    for dim, base in dim_base.items():
        for sub_idx in range(3):
            # 子能力对应题目：Q(base+sub_idx*2) 和 Q(base+sub_idx*2+1)
            q1 = base + sub_idx * 2
            q2 = q1 + 1
            score = (normalized.get(q1, 0) + normalized.get(q2, 0)) / 2
            key = (dim, sub_idx)
            sub_name, sub_desc = sub_competencies.get(key, (f'子能力{sub_idx+1}', ''))
            results.append({
                'dim': dim,
                'sub_idx': sub_idx,
                'name': sub_name,
                'desc': sub_desc,
                'score': round(score, 2),
                'level': get_level_label(score)
            })

    # 按分数升序排列（最弱的在前面）
    results.sort(key=lambda x: x['score'])
    return results

def get_level(score):
    if score >= 4.0: return '优秀'
    elif score >= 3.0: return '良好'
    elif score >= 2.0: return '待提升'
    else: return '需加强'

def check_validity(answers, expected=31):
    answered_count = sum(1 for v in answers.values() if v > 0)
    if answered_count < expected:
        return {'is_valid': False, 'reason': '未完成所有题目'}
    if answers:
        option_counts = {}
        for v in answers.values():
            option_counts[v] = option_counts.get(v, 0) + 1
        max_count = max(option_counts.values())
        if max_count / len(answers) > 0.8:
            return {'is_valid': False, 'reason': '作答规律性过强'}
    return {'is_valid': True}

def generate_pdf(result_id, scores, user_name, industry, experience):
    """
    生成PDF报告（8维版 V4设计）
    包装函数 - 调用 generate_pdf_48_v4()
    注意：generate_pdf_48_v4() 不使用 industry 参数
    """
    return generate_pdf_48_v4(result_id, scores, user_name, experience, font_name=CHINESE_FONT)


HTML_GATEWAY = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>8维能力测评 | Santa Chow</title>
    <style>
        *{margin:0;padding:0;box-sizing:border-box}
        body{
            font-family:"Microsoft YaHei","PingFang SC","Noto Sans SC",sans-serif;
            background:linear-gradient(135deg,#0f172a 0%,#1e3a5f 50%,#0f172a 100%);
            min-height:100vh;display:flex;align-items:center;justify-content:center;
            padding:20px;
        }
        .card{
            background:rgba(255,255,255,0.97);border-radius:20px;
            padding:50px 40px;max-width:480px;width:100%;
            box-shadow:0 25px 60px rgba(0,0,0,0.4);
            text-align:center;
        }
        .logo{font-size:12px;letter-spacing:4px;color:#f59e0b;font-weight:600;margin-bottom:8px;}
        h1{font-size:28px;color:#0f172a;margin-bottom:8px;letter-spacing:1px;}
        .subtitle{color:#64748b;font-size:15px;margin-bottom:40px;line-height:1.6;}
        .btn{
            width:100%;padding:16px;background:linear-gradient(135deg,#1e3a8a,#3b82f6);
            color:white;border:none;border-radius:12px;font-size:17px;
            font-weight:600;cursor:pointer;transition:opacity 0.2s;letter-spacing:2px;
            text-decoration:none;display:inline-block;
        }
        .btn:hover{opacity:0.9;}
        .note{font-size:12px;color:#94a3b8;margin-top:16px;}
        .features{display:flex;gap:20px;margin-bottom:36px;justify-content:center;}
        .feature{background:#f8fafc;border-radius:10px;padding:14px 12px;flex:1;}
        .feature .num{font-size:22px;font-weight:700;color:#1e3a8a;}
        .feature .txt{font-size:12px;color:#64748b;margin-top:4px;}
        @keyframes fadeIn{from{opacity:0;transform:translateY(20px)}to{opacity:1;transform:translateY(0)}}
        .card{animation:fadeIn 0.5s ease-out;}
    </style>
</head>
<body>
    <div class="card">
        <div class="logo">SANTA CHOW</div>
        <h1>8维能力测评</h1>
        <p class="subtitle">48题 · 约10分钟 · 科学评估你的职场核心能力</p>
        <div class="features">
            <div class="feature">
                <div class="num">8</div>
                <div class="txt">核心维度</div>
            </div>
            <div class="feature">
                <div class="num">48</div>
                <div class="txt">道测评题</div>
            </div>
            <div class="feature">
                <div class="num">10</div>
                <div class="txt">分钟完成</div>
            </div>
        </div>
        <a href="/quiz" class="btn">开始答题</a>
        <p class="note">由 Santa Chow 提供 · 港漂职场竞争力评估</p>
    </div>
</body>
</html>"""


@app.route('/')
def index():
    """入口页面"""
    return HTML_GATEWAY


@app.route('/quiz')
def quiz_page():
    """8维能力测评答题页面（无需token，直接访问）"""
    quiz_path = os.path.join(os.path.dirname(__file__), '8d_quiz_48.html')
    try:
        with open(quiz_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return jsonify({'error': 'Quiz page not found'}), 404

@app.route('/8d_quiz_48.html')
def quiz_legacy():
    """兼容旧链接，直接展示问卷"""
    quiz_path = os.path.join(os.path.dirname(__file__), '8d_quiz_48.html')
    try:
        with open(quiz_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return jsonify({'error': 'Quiz page not found'}), 404

@app.route('/quiz48')
def quiz48():
    """Alias for the quiz"""
    quiz_path = os.path.join(os.path.dirname(__file__), '8d_quiz_48.html')
    try:
        with open(quiz_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return jsonify({'error': 'Quiz page not found'}), 404

@app.route('/admin')
def admin():
    return HTML_ADMIN

@app.route('/api/health')
def health():
    return jsonify({'status': 'ok', 'timestamp': datetime.now().isoformat(), 'font': CHINESE_FONT or 'none'})

# ---------- JWT 相关 ----------
JWT_SECRET = os.environ.get('JWT_SECRET', 'change_me_secret')  # 请在 Render 环境变量中设置安全的密钥
JWT_ALGO = 'HS256'

def generate_token(user_id, role):
    payload = {
        'sub': str(user_id),
        'role': role,
        'iat': datetime.utcnow(),
        'exp': datetime.utcnow().replace(microsecond=0) + __import__('datetime').timedelta(hours=4)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)

def decode_token(token):
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

def jwt_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get('Authorization', None)
        if not auth or not auth.startswith('Bearer '):
            return jsonify({'error': 'Missing or invalid Authorization header'}), 401
        token = auth.split(' ')[1]
        payload = decode_token(token)
        if not payload:
            return jsonify({'error': 'Invalid or expired token'}), 401
        request.jwt_payload = payload
        return f(*args, **kwargs)
    return decorated

# ---------- Admin 权限检查 ----------
def admin_required(f):
    @wraps(f)
    @jwt_required
    def wrapper(*args, **kwargs):
        payload = request.jwt_payload
        if payload.get('role') != 'admin':
            return jsonify({'error': 'Admin privilege required'}), 403
        return f(*args, **kwargs)
    return wrapper

# ---------- 登录接口 ----------
@app.route('/admin/login', methods=['POST'])
def admin_login():
    data = request.get_json(silent=True) or {}
    username = data.get('username')
    password = data.get('password')
    if not username or not password:
        return jsonify({'msg': 'username and password required'}), 400
    with get_db() as conn:
        cur = conn.execute('SELECT id, password_hash, role FROM admin_user WHERE username = ?', (username,))
        row = cur.fetchone()
        if not row:
            return jsonify({'msg': 'Invalid credentials'}), 401
        user_id, pwd_hash, role = row
        if not check_password_hash(pwd_hash, password):
            return jsonify({'msg': 'Invalid credentials'}), 401
        token = generate_token(user_id, role)
        return jsonify({'token': token, 'role': role, 'username': username})

@app.route('/api/admin/check', methods=['GET'])
@admin_required
def admin_check():
    """验证当前会话是否有效（前端轮询/页面加载时调用）"""
    payload = request.jwt_payload
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT username FROM admin_user WHERE id = ?', (int(payload.get('sub')),))
        row = c.fetchone()
    return jsonify({'logged_in': True, 'username': row[0] if row else 'admin', 'role': payload.get('role')})

@app.route('/api/quiz/submit', methods=['POST'])
def submit():
    try:
        data = request.get_json()
        if not data or 'answers' not in data:
            return jsonify({'error': 'Missing answers'}), 400

        scores = calculate_scores(data['answers'])
        validity = check_validity(data['answers'])

        with get_db() as conn:
            c = conn.cursor()
            c.execute('''INSERT INTO quiz_results
                (user_name, industry, experience, answers, question_order, scores, validity_check, submitted_at, ip_address, user_agent)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (data.get('name', '匿名用户'), data.get('industry', ''), data.get('experience', ''),
                 json.dumps(data.get('answers', {})), json.dumps(data.get('question_order', [])),
                 json.dumps(scores), 1 if validity['is_valid'] else 0,
                 datetime.now().isoformat(), request.remote_addr, request.headers.get('User-Agent', '')))
            result_id = c.lastrowid

        return jsonify({'success': True, 'result_id': result_id, 'scores': scores, 'validity': validity})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/quiz/report/<int:result_id>')
@jwt_required
def report(result_id):
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute('SELECT * FROM quiz_results WHERE id = ?', (result_id,))
            row = c.fetchone()

        if not row:
            return jsonify({'error': 'Not found'}), 404

        scores = json.loads(row['scores'])
        pdf_buffer = generate_pdf(row['id'], scores, row['user_name'], row['industry'], row['experience'])

        return send_file(pdf_buffer, mimetype='application/pdf',
                        as_attachment=True,
                        download_name=f'7d_report_{row["user_name"]}_{result_id}.pdf')
    except Exception as e:
        import traceback
        print(f"PDF生成错误: {traceback.format_exc()}")
        return jsonify({'error': str(e), 'font_available': CHINESE_FONT is not None, 'font_name': CHINESE_FONT or 'none'}), 500

@app.route('/api/quiz/all')
@admin_required
def get_all():
    try:
        name = request.args.get('name', '')
        industry = request.args.get('industry', '')
        limit = int(request.args.get('limit', 100))

        with get_db() as conn:
            c = conn.cursor()
            conditions = ['1=1']
            params = []
            if name:
                conditions.append('user_name LIKE ?')
                params.append(f'%{name}%')
            if industry:
                conditions.append('industry = ?')
                params.append(industry)

            c.execute(f'SELECT * FROM quiz_results WHERE {" AND ".join(conditions)} ORDER BY id DESC LIMIT {limit}')
            rows = c.fetchall()
            c.execute('SELECT DISTINCT industry FROM quiz_results WHERE industry IS NOT NULL AND industry != ""')
            industries = [r['industry'] for r in c.fetchall()]

        return jsonify({'results': [dict(r) for r in rows], 'industries': industries})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/quiz/export')
@admin_required
def export():
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute('SELECT * FROM quiz_results ORDER BY id DESC')
            rows = c.fetchall()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['ID', '姓名', '行业', '年限', '提交时间', '有效性'])
        for r in rows:
            writer.writerow([r['id'], r['user_name'], r['industry'], r['experience'], r['submitted_at'], r['validity_check']])

        output.seek(0)
        return send_file(io.BytesIO(output.getvalue().encode('utf-8-sig')),
                        mimetype='text/csv',
                        as_attachment=True,
                        download_name=f'quiz_results_{datetime.now().strftime("%Y%m%d")}.csv')
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/quiz/batch-import', methods=['POST'])
@admin_required
def batch_import():
    try:
        file = request.files.get('file')
        if not file:
            return jsonify({'error': 'No file'}), 400

        content = file.read().decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(content))

        success_count = 0
        fail_count = 0

        with get_db() as conn:
            c = conn.cursor()
            for row in reader:
                try:
                    answers = {f'q{i}': int(row.get(f'q{i}', 0)) for i in range(1, 32)}
                    scores = calculate_scores(answers)
                    validity = check_validity(answers)

                    c.execute('''INSERT INTO quiz_results
                        (user_name, industry, experience, answers, question_order, scores, validity_check, submitted_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                        (row.get('name', '匿名'), row.get('industry', ''), row.get('experience', ''),
                         json.dumps(answers), '[]', json.dumps(scores), 1 if validity['is_valid'] else 0,
                         datetime.now().isoformat()))
                    success_count += 1
                except:
                    fail_count += 1
            conn.commit()

        return jsonify({'success': True, 'message': f'导入完成', 'success_count': success_count, 'fail_count': fail_count})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============ 人格画像 + 张力分析 + 缺陷重塑引擎 ============
def generate_personality_analysis(scores):
    """
    基于8维评分生成：人格画像 / 张力分析 / 缺陷重塑报告数据。
    由 submit_48 自动调用，结果嵌入JSON响应。
    """
    if not scores:
        return {}

    dim_names = {
        'COG': '认知能力', 'TEC': '技术能力', 'COM': '表达能力',
        'SOC': '社交能力', 'ORG': '策划执行', 'PRS': '解决问题',
        'MGT': '管理能力', 'LLA': '持续学习'
    }
    dim_en = {
        'COG': 'Cognitive', 'TEC': 'Technical', 'COM': 'Communication',
        'SOC': 'Social', 'ORG': 'Organization', 'PRS': 'Problem Solving',
        'MGT': 'Management', 'LLA': 'Lifelong Learning'
    }

    def avg(d):
        return d.get('average', 0)

    # ── 维度高低判定 ──
    is_high = lambda d: avg(scores.get(d, {})) >= 3.5
    is_low  = lambda d: avg(scores.get(d, {})) < 3.0

    cog_h, tec_h, com_h = is_high('COG'), is_high('TEC'), is_high('COM')
    soc_h, mgt_h, lla_h = is_high('SOC'), is_high('MGT'), is_high('LLA')
    org_h, prs_h         = is_high('ORG'), is_high('PRS')

    cog_l, tec_l, com_l = is_low('COG'), is_low('TEC'), is_low('COM')
    soc_l, mgt_l, lla_l = is_low('SOC'), is_low('MGT'), is_low('LLA')
    org_l, prs_l         = is_low('ORG'), is_low('PRS')

    # ── 维度组聚类 ──
    # 思维表达组：COG+TEC+COM（信息处理与输出）
    thinking = 'analytical' if (cog_h and (tec_h or com_h)) else \
               'pragmatic' if (tec_h and not cog_h) else \
               'expressive' if (com_h and not cog_h and not tec_h) else \
               'foundational'
    # 人际取向组：SOC+MGT+LLA（人际连接与影响）
    social   = 'connector' if (soc_h and not mgt_h) else \
               'leader'    if (mgt_h and soc_h) else \
               'specialist' if (lla_h and not soc_h and not mgt_h) else \
               'independent'
    # 执行模式组：ORG+PRS（目标达成方式）
    execution = 'strategic'  if (org_h and prs_h) else \
                'executor'    if (org_h and not prs_h) else \
                'problem-solver' if (prs_h and not org_h) else \
                'adaptive'

    # ── 16型人格画像库 ──
    profile_map = {
        ('analytical', 'connector', 'strategic'): ('远见策划型', '战略协调者', '创新布道'),
        ('analytical', 'connector', 'executor'):   ('思想领袖型', '知识传播者', '系统建造'),
        ('analytical', 'connector', 'problem-solver'): ('创新倡导型', '跨界连接者', '洞察驱动'),
        ('analytical', 'connector', 'adaptive'):  ('智慧顾问型', '认知导师', '知识架构'),
        ('analytical', 'leader', 'strategic'):    ('战略指挥官', '全局架构', '决策引擎'),
        ('analytical', 'leader', 'executor'):    ('思想管理者', '知识整合', '执行智囊'),
        ('analytical', 'leader', 'problem-solver'): ('破局策划型', '方案设计', '模式重构'),
        ('analytical', 'leader', 'adaptive'):    ('智库策略型', '战略规划', '方法论设计'),
        ('analytical', 'specialist', 'strategic'): ('技术战略型', '深度专才', '架构思维'),
        ('analytical', 'specialist', 'executor'): ('技术作家型', '知识沉淀', '文档大师'),
        ('analytical', 'specialist', 'problem-solver'): ('研究专家型', '学术探究', '深度钻研'),
        ('analytical', 'specialist', 'adaptive'): ('技术顾问型', '解决方案', '专业咨询'),
        ('analytical', 'independent', 'strategic'): ('独立策划型', '自由思考', '个人战略'),
        ('analytical', 'independent', 'executor'): ('独立建造型', '自驱实现', '工匠精神'),
        ('analytical', 'independent', 'problem-solver'): ('独立分析师', '自我提升', '深度思考'),
        ('analytical', 'independent', 'adaptive'): ('独立顾问型', '自我迭代', '持续进化'),
        # ── 务实 pragmatics ──
        ('pragmatic', 'connector', 'strategic'):  ('业务策划型', '落地推动', '资源整合'),
        ('pragmatic', 'connector', 'executor'):   ('业务拓展型', '关系推动', '机会捕捉'),
        ('pragmatic', 'connector', 'problem-solver'): ('问题协调型', '关系解决', '冲突处理'),
        ('pragmatic', 'connector', 'adaptive'):   ('机会探索型', '敏锐嗅探', '关系拓展'),
        ('pragmatic', 'leader', 'strategic'):    ('业务指挥官', '目标驱动', '资源调配'),
        ('pragmatic', 'leader', 'executor'):     ('执行领袖型', '结果导向', '团队驱动'),
        ('pragmatic', 'leader', 'problem-solver'): ('破局领袖型', '快速决断', '逆境突破'),
        ('pragmatic', 'leader', 'adaptive'):      ('业务舵手型', '市场嗅觉', '灵活调度'),
        ('pragmatic', 'specialist', 'strategic'): ('技术策划型', '工具思维', '效率优化'),
        ('pragmatic', 'specialist', 'executor'):  ('技术工匠型', '精耕细作', '品质保障'),
        ('pragmatic', 'specialist', 'problem-solver'): ('技术医师型', '故障排除', '系统修复'),
        ('pragmatic', 'specialist', 'adaptive'):   ('技术通才型', '多工具', '适应力强'),
        ('pragmatic', 'independent', 'strategic'): ('独立策划型', '自我管理', '目标明确'),
        ('pragmatic', 'independent', 'executor'):  ('独立工匠型', '自我驱动', '高质量输出'),
        ('pragmatic', 'independent', 'problem-solver'): ('独立医师型', '自我诊断', '自主解决'),
        ('pragmatic', 'independent', 'adaptive'):  ('独立通才型', '多面手', '灵活适应'),
        # ── 表达型 expressives ──
        ('expressive', 'connector', 'strategic'):  ('魅力策划型', '愿景描绘', '人心凝聚'),
        ('expressive', 'connector', 'executor'):  ('魅力行动型', '热情驱动', '人际带动'),
        ('expressive', 'connector', 'problem-solver'): ('魅力协调型', '人心解决', '关系润滑'),
        ('expressive', 'connector', 'adaptive'):  ('魅力探索型', '社交敏锐', '机会链接'),
        ('expressive', 'leader', 'strategic'):   ('激励指挥官', '愿景领导', '战略传播'),
        ('expressive', 'leader', 'executor'):    ('激励执行型', '人心驱动', '团队激活'),
        ('expressive', 'leader', 'problem-solver'): ('激励破局型', '人心突破', '共识构建'),
        ('expressive', 'leader', 'adaptive'):    ('激励舵手型', '氛围营造', '人心调度'),
        ('expressive', 'specialist', 'strategic'): ('专业布道型', '知识传播', '理念推广'),
        ('expressive', 'specialist', 'executor'): ('专业导师型', '教学相长', '经验传承'),
        ('expressive', 'specialist', 'problem-solver'): ('专业协调型', '咨询顾问', '方案解读'),
        ('expressive', 'specialist', 'adaptive'):  ('专业探索型', '多元学习', '跨界应用'),
        ('expressive', 'independent', 'strategic'): ('独立布道型', '个人品牌', '影响力扩散'),
        ('expressive', 'independent', 'executor'): ('独立导师型', '自我实现', '经验沉淀'),
        ('expressive', 'independent', 'problem-solver'): ('独立协调型', '自主咨询', '问题洞察'),
        ('expressive', 'independent', 'adaptive'): ('独立导师型', '自我迭代', '多元发展'),
        # ── 基础型 foundational ──
        ('foundational', 'connector', 'strategic'): ('成长策划型', '协作学习', '目标聚焦'),
        ('foundational', 'connector', 'executor'):  ('成长行动型', '协作驱动', '稳步推进'),
        ('foundational', 'connector', 'problem-solver'): ('成长协调型', '协作问题解决', '关系维护'),
        ('foundational', 'connector', 'adaptive'): ('成长探索型', '协作适应', '机会感知'),
        ('foundational', 'leader', 'strategic'):   ('成长指挥官', '带队成长', '目标管理'),
        ('foundational', 'leader', 'executor'):    ('成长执行型', '带队实践', '稳步落地'),
        ('foundational', 'leader', 'problem-solver'): ('成长破局型', '带队解决问题', '集体突破'),
        ('foundational', 'leader', 'adaptive'):    ('成长舵手型', '带队适应', '灵活调度'),
        ('foundational', 'specialist', 'strategic'): ('专精策划型', '垂直深耕', '体系建立'),
        ('foundational', 'specialist', 'executor'): ('专精执行型', '专业积累', '品质沉淀'),
        ('foundational', 'specialist', 'problem-solver'): ('专精医师型', '故障专研', '系统理解'),
        ('foundational', 'specialist', 'adaptive'): ('专精适应型', '专业拓展', '跨界整合'),
        ('foundational', 'independent', 'strategic'): ('独立成长型', '自学成才', '自我管理'),
        ('foundational', 'independent', 'executor'):  ('独立工匠型', '自我驱动', '踏实积累'),
        ('foundational', 'independent', 'problem-solver'): ('独立医师型', '自我摸索', '试错学习'),
        ('foundational', 'independent', 'adaptive'): ('独立适应型', '自我迭代', '持续成长'),
    }

    key = (thinking, social, execution)
    primary, secondary, third = profile_map.get(key, ('多元复合型', '适应性人才', '持续进化'))
    tag  = f"{primary} · {secondary} · {third}"

    # ── 维度性格解读 ──
    cog_s = avg(scores.get('COG', {})); tec_s = avg(scores.get('TEC', {}))
    com_s = avg(scores.get('COM', {})); soc_s = avg(scores.get('SOC', {}))
    org_s = avg(scores.get('ORG', {})); prs_s = avg(scores.get('PRS', {}))
    mgt_s = avg(scores.get('MGT', {})); lla_s = avg(scores.get('LLA', {}))

    cog_lbl = '强分析' if cog_h else ('弱分析' if cog_l else '均衡')
    tec_lbl = '技术型' if tec_h else ('非技术' if tec_l else '均衡')
    com_lbl = '表达型' if com_h else ('内敛型' if com_l else '均衡')
    soc_lbl = '社交型' if soc_h else ('独立型' if soc_l else '均衡')
    org_lbl = '策划型' if org_h else ('执行型' if org_l else '均衡')
    prs_lbl = '解决型' if prs_h else ('稳健型' if prs_l else '均衡')
    mgt_lbl = '管理型' if mgt_h else ('执行型' if mgt_l else '均衡')
    lla_lbl = '学习型' if lla_h else ('经验型' if lla_l else '均衡')

    # ── 行为特征 ──
    traits = []
    if cog_h and prs_h:  traits.append('理性驱动，习惯从根源解决问题')
    if cog_h and soc_h:  traits.append('兼具洞察力与同理心，影响力强')
    if tec_h and org_h:  traits.append('擅长用系统化方式推进目标')
    if tec_h and soc_h:  traits.append('技术+人际双轨，能推动团队落地')
    if com_h and soc_h:  traits.append('表达生动，善于凝聚共识与调动氛围')
    if org_h and prs_h:  traits.append('有战略眼光，既能规划又能突破')
    if mgt_h and org_h:  traits.append('具备全局视角，擅长目标分解与资源调配')
    if lla_h and cog_h:  traits.append('终身学习者，知识更新速度快')
    if cog_l and soc_h:  traits.append('以关系为中心，直觉判断多于逻辑分析')
    if tec_h and lla_l:  traits.append('技术积累扎实，但更新意愿较低')
    if org_h and prs_l:  traits.append('规划能力强，执行落地节奏偏慢')
    if soc_l and mgt_h:  traits.append('管理欲望强，但更倾向于独立工作')
    if not traits:       traits.append('能力均衡，适应性强，角色灵活')

    # ── 张力分析（最多4条） ──
    tensions = []
    tension_score = 0

    def add_tension(score_add, type_code, headline, detail):
        nonlocal tension_score
        tensions.append({'type': type_code, 'headline': headline, 'detail': detail})
        tension_score += score_add

    if tec_h and soc_l:
        add_tension(20, 'tech-vs-social', '技术深度 vs 社交意愿',
                    f'技术能力({tec_s:.1f})突出，但社交意愿({soc_s:.1f})偏低。'
                    '容易沉浸独立解决问题，错失协作带来的杠杆效应。建议主动参与跨团队项目，将技术价值扩散。')
    elif soc_h and tec_l:
        add_tension(15, 'social-vs-tech', '人际热度 vs 技术深度',
                    f'社交能力({soc_s:.1f})突出，但技术基础({tec_s:.1f})偏弱。'
                    '依赖关系网络而非专业壁垒建立影响力。建议培养一项可量化的核心技术能力。')

    if org_h and prs_l:
        add_tension(20, 'plan-vs-execute', '策划宏大 vs 落地迟缓',
                    f'策划能力({org_s:.1f})强，但解决问题敏捷性({prs_s:.1f})偏低。'
                    '方案完善但推进速度慢。建议缩短方案迭代周期，用"72小时行动规则"强制落地。')
    elif prs_h and org_l:
        add_tension(15, 'solve-vs-plan', '快速解决 vs 缺乏规划',
                    f'解决问题({prs_s:.1f})敏捷，但策划规划({org_s:.1f})偏弱。'
                    '频繁救火，缺乏长期主线。建议用OKR框架锁定季度目标，每月复盘纠偏。')

    if soc_h and mgt_l:
        add_tension(20, 'social-vs-mgmt', '关系优先 vs 管理回避',
                    f'社交热情({soc_s:.1f})高，但管理意愿({mgt_s:.1f})低。'
                    '善于建立关系但回避主导角色，可能错失影响他人的机会。建议从"项目协调"角色切入，积累管理信心。')
    elif mgt_h and soc_l:
        add_tension(15, 'mgmt-vs-social', '管理野心 vs 社交孤立',
                    f'管理能力({mgt_s:.1f})强，但社交连接({soc_s:.1f})弱。'
                    '推动力足但人心凝聚不足。建议每季度建立2-3个跨部门弱连接，扩展影响力网络。')

    if tec_h and lla_l:
        add_tension(25, 'tech-vs-stagnate', '技术积累深 vs 更新停滞',
                    f'技术适应({tec_s:.1f})强，但学习意愿({lla_s:.1f})低。'
                    '当前技术优势可能在3-5年后被淘汰。建议每季度强制学习1项新工具，以"教别人"的方式输出。')
    elif lla_h and tec_l:
        add_tension(20, 'learn-vs-apply', '学习欲强 vs 技术落地弱',
                    f'持续学习({lla_s:.1f})强，但技术掌握({tec_s:.1f})偏弱。'
                    '知识广度够但深度不足，容易"样样通样样松"。建议选择1个领域深耕6个月，形成专业壁垒。')

    if com_h and cog_l:
        add_tension(15, 'express-vs-think', '表达流畅 vs 深度不足',
                    f'表达能力({com_s:.1f})强，但认知深度({cog_s:.1f})偏低。'
                    '输出量大但洞察浅。建议每季度精读1本领域经典著作，写结构化笔记深化思考。')
    elif cog_h and com_l:
        add_tension(10, 'think-vs-express', '深度思考 vs 表达钝化',
                    f'认知能力({cog_s:.1f})强，但表达({com_s:.1f})偏弱。'
                    '有真知灼见但难以传递给他人。建议练习"3分钟电梯演讲"，强制结构化输出。')

    if soc_l and mgt_l and com_h:
        add_tension(15, 'solo-expert', '独狼专家陷阱',
                    '社交、管理双低+表达偏高，倾向独自钻研后单点输出。'
                    '影响力天花板明显。建议主动承担"知识传播者"角色，从写文章开始扩大影响半径。')

    if org_l and prs_l and lla_l:
        add_tension(30, 'drift-risk', '能力漂移风险',
                    '策划、解决问题、持续学习三项均偏低，职业方向可能模糊。'
                    '建议尽快用职业兴趣测评锁定1-2个方向，每方向深耕3个月试错验证。')
    elif org_h and prs_h and mgt_h:
        add_tension(20, 'burnout-risk', '高期望高压风险',
                    '策划、解决问题、管理三项均高，自我期待极高，容易过度消耗。'
                    '建议建立"能量边界"机制，每周预留1天完全不工作，防止职业倦怠。')

    tension_score = min(100, tension_score)

    # ── 缺陷重塑（来自 BOT 3 维度） ──
    bot3 = sorted(scores.items(), key=lambda x: x[1]['average'])[:3]
    defects = []
    defect_actions = {}

    for dim, s in bot3:
        v = s['average']
        d_name = dim_names.get(dim, dim)
        d_en    = dim_en.get(dim, dim)

        if dim == 'COG':
            defect_actions[dim] = {
                'sign': '信息过载时判断迟缓，容易被细节淹没',
                'action': '建立信息过滤漏斗：每日上午10点前只处理"与目标直接相关"的信息，用5Why追问法提取核心。',
                'resource': '《思考，快与慢》+ 金字塔原理'
            }
        elif dim == 'TEC':
            defect_actions[dim] = {
                'sign': '对新技术保持观望，倾向于用旧工具解决新问题',
                'action': '每季度设定"探索日"，强制用新工具完成1件日常任务（如用Notion重构工作流）。',
                'resource': 'Product Hunt 每日精选 + Coursera 短期课程'
            }
        elif dim == 'COM':
            defect_actions[dim] = {
                'sign': '表达冗长或难以抓住重点，沟通效率低',
                'action': '练习"电梯演讲"：任何话题必须在3句话内说明核心观点。用晨间写作（每天300字）锻炼结构化表达。',
                'resource': '《金字塔原理》+ TED演讲结构'
            }
        elif dim == 'SOC':
            defect_actions[dim] = {
                'sign': '倾向独自工作，对人际互动的能量消耗感强',
                'action': '每周主动发起1次"15分钟咖啡聊"（线上即可），逐步建立关系舒适区。',
                'resource': 'LinkedIn 社交策略 + 《人性的弱点》'
            }
        elif dim == 'ORG':
            defect_actions[dim] = {
                'sign': '有目标但执行节奏慢，计划赶不上变化',
                'action': '将季度目标拆解为每周3个"必须完成"（MIT），每天早晨写3个MIT，强制优先执行。',
                'resource': 'OKR工作法 + 《高效能人士的七个习惯》'
            }
        elif dim == 'PRS':
            defect_actions[dim] = {
                'sign': '遇到复杂问题时习惯等待更多信息，错失行动窗口',
                'action': '采用"最坏情况预案"：有60%信息时就做出初步决策，用Plan B兜底，而非等待100%确定性。',
                'resource': '《零点思考》+ 麦肯锡问题解决7步'
            }
        elif dim == 'MGT':
            defect_actions[dim] = {
                'sign': '倾向于亲力亲为，难以有效授权',
                'action': '每周选择1件可委托的事，明确"期望结果"而非"执行方式"，强制自己只追踪里程碑。',
                'resource': '《卓有成效的管理者》+ 情境领导力'
            }
        elif dim == 'LLA':
            defect_actions[dim] = {
                'sign': '学习停留在舒适区，缺乏主动更新知识的意识',
                'action': '建立"学习输出"机制：每学完一项内容，必须写1篇笔记或教给别人1次，否则视为未完成。',
                'resource': '费曼学习法 + Obsidian 知识管理系统'
            }

        defects.append({
            'dimension': dim,
            'name': d_name,
            'en': d_en,
            'score': v,
            **defect_actions.get(dim, {'sign': '有待发展', 'action': '建议针对性训练', 'resource': ''})
        })

    # ── 综合结论 ──
    all_scores = [v['average'] for v in scores.values()]
    overall = sum(all_scores) / len(all_scores) if all_scores else 0
    top3_local = sorted(scores.items(), key=lambda x: x[1]['average'], reverse=True)[:3]
    conclusion = (
        f"你的核心优势集中在{dim_names.get(top3_local[0][0],'') if top3_local else '综合能力'}，"
        f"建议持续强化这一优势作为职业壁垒。"
        f"当前最高张力为「{tensions[0]['headline']}」（{tension_score}%），"
        f"优先解决该张力可带来最大的能力提升杠杆。"
        f"最需要发展的维度是{dim_names.get(bot3[0][0],'') if bot3 else '综合能力'}，"
        f"建议从{bot3[0][0] if bot3 else ''}维度入手制定90天行动计划。"
    ) if tensions else (
        f"你的8维能力分布均衡，整体综合评分{overall:.1f}/5.0，具备良好的职业适应性。"
        f"核心优势为{dim_names.get(top3_local[0][0],'')}，建议持续深耕形成差异化竞争力。"
    )

    return {
        'profile': {
            'type': primary,
            'sub_type': secondary,
            'third_type': third,
            'tag': tag,
            'thinking': thinking,
            'social': social,
            'execution': execution,
            'dimension_labels': {
                'COG': cog_lbl, 'TEC': tec_lbl, 'COM': com_lbl,
                'SOC': soc_lbl, 'ORG': org_lbl, 'PRS': prs_lbl,
                'MGT': mgt_lbl, 'LLA': lla_lbl
            },
            'traits': traits,
            'overall_score': round(overall, 1),
            'top_dimension': dim_names.get(top3_local[0][0] if top3_local else '', ''),
            'growth_dimension': dim_names.get(bot3[0][0] if bot3 else '', '')
        },
        'tension_analysis': {
            'score': tension_score,
            'level': '高危' if tension_score >= 60 else '中危' if tension_score >= 30 else '低危',
            'items': tensions[:4],
            'summary': f'检测到{len(tensions)}项核心张力，综合张力指数{tension_score}%，{"建议优先解决最高张力项" if tensions else "能力分布相对均衡"}'
        },
        'defect_reshaping': {
            'areas': defects,
            'top_priority': defects[0] if defects else None,
            'action_plan': f'90天行动计划：从{dim_names.get(bot3[0][0],'') if bot3 else "综合"}维度切入，'
                           f'每30天完成1次能力里程碑自检，90天后复盘提升幅度。'
        },
        'conclusion': conclusion
    }


# ============ 8D 48题新增路由 ============

@app.route('/api/quiz/submit_48', methods=['POST'])
def submit_48():
    """提交48题8维测评，同时触发人格画像+张力分析+缺陷重塑报告引擎"""
    try:
        data = request.get_json()
        if not data or 'answers' not in data:
            return jsonify({'error': 'Missing answers'}), 400

        scores = calculate_scores_48(data['answers'])

        with get_db() as conn:
            c = conn.cursor()
            c.execute('''INSERT INTO quiz_results_48
                (user_name, experience, industry, answers, question_order, scores, submitted_at, ip_address, user_agent)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (data.get('name', '匿名用户'), data.get('experience', ''),
                 data.get('industry', ''), json.dumps(data.get('answers', {})),
                 json.dumps(data.get('question_order', [])), json.dumps(scores),
                 datetime.now().isoformat(), request.remote_addr,
                 request.headers.get('User-Agent', '')))
            result_id = c.lastrowid
            conn.commit()

        # ── 自动触发人格画像 + 张力分析 + 缺陷重塑引擎 ──
        analysis = generate_personality_analysis(scores)

        return jsonify({
            'success': True,
            'result_id': result_id,
            'scores': scores,
            # ── 人格画像 + 张力分析 + 缺陷重塑报告 ──
            'personality_report': analysis
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/quiz/report_48/<int:result_id>')
def report_48(result_id):
    """生成48题PDF报告（V4专业设计版）"""
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute('SELECT * FROM quiz_results_48 WHERE id = ?', (result_id,))
            row = c.fetchone()

        if not row:
            return jsonify({'error': 'Not found'}), 404

        scores = json.loads(row['scores'])
        # 读取 answers 用于计算子能力分数
        answers = json.loads(row['answers']) if row['answers'] else {}
        pdf_buffer = generate_pdf_48_v4(result_id, scores, answers, row['user_name'], row['experience'],
                                        font_name=CHINESE_FONT or 'Helvetica')
        report_date = datetime.now().strftime("%Y%m%d")

        return send_file(pdf_buffer, mimetype='application/pdf',
                        as_attachment=True,
                        download_name=f'8d_report_{row["user_name"]}_{report_date}.pdf')
    except Exception as e:
        import traceback
        print(f"PDF生成错误: {traceback.format_exc()}")
        return jsonify({'error': str(e), 'font_available': CHINESE_FONT is not None,
                       'font_name': CHINESE_FONT or 'none'}), 500


@app.route('/api/quiz/report_48_v2/<int:result_id>')
def report_48_v2(result_id):
    """生成48题PDF报告 V2（雷达图 + 颜色编码 + 场景化举例）"""
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute('SELECT * FROM quiz_results_48 WHERE id = ?', (result_id,))
            row = c.fetchone()

        if not row:
            return jsonify({'error': 'Not found'}), 404

        scores = json.loads(row['scores'])
        answers = json.loads(row['answers']) if row['answers'] else {}
        pdf_buffer = generate_pdf_48_v2(row['id'], scores, answers, row['user_name'], row['experience'])
        report_date = datetime.now().strftime("%Y%m%d")

        return send_file(pdf_buffer, mimetype='application/pdf',
                        as_attachment=True,
                        download_name=f'8d_report_V2_{row["user_name"]}_{report_date}.pdf')
    except Exception as e:
        import traceback
        print(f"V2 PDF生成错误: {traceback.format_exc()}")
        return jsonify({'error': str(e), 'font_available': CHINESE_FONT is not None, 'font_name': CHINESE_FONT or 'none'}), 500


# ============ V2 报告颜色常量 ============
COLOR_EXCELLENT = colors.HexColor('#1e3a8a')   # 深蓝（优秀）
COLOR_GOOD = colors.HexColor('#3b82f6')        # 亮蓝（良好）
COLOR_IMPROVE = colors.HexColor('#ea580c')     # 暖橘（待提升）
COLOR_BG_TOP = colors.HexColor('#eff6ff')      # 浅蓝底（核心优势区）
COLOR_BG_BOT = colors.HexColor('#fff7ed')      # 暖橙底（发展空间区）
COLOR_BG_WHITE = colors.white
COLOR_WHITE = colors.white
COLOR_TEXT_DARK = colors.HexColor('#1e293b')
COLOR_TEXT_MUTED = colors.HexColor('#64748b')
COLOR_GRID = colors.HexColor('#e2e8f0')

def get_score_color(score):
    """根据分数返回对应颜色"""
    if score >= 4.0:
        return COLOR_EXCELLENT
    elif score >= 3.0:
        return COLOR_GOOD
    else:
        return COLOR_IMPROVE

def get_level_label(score):
    """根据分数返回等级标签"""
    if score >= 4.5: return '优秀'
    elif score >= 4.0: return '良好+'
    elif score >= 3.5: return '良好'
    elif score >= 3.0: return '中等'
    elif score >= 2.5: return '待提升'
    else: return '需加强'

def draw_radar_chart_v2(dims, scores, width=220, height=220):
    """绘制 V2 雷达图（8维完整显示）"""
    from reportlab.graphics.shapes import Drawing, Polygon, String, Line, Circle
    import math

    d = Drawing(width, height)
    cx, cy = width // 2, height // 2
    radius = 80
    n = len(dims)

    # 绘制半透明背景圆
    d.add(Circle(cx, cy, radius, strokeColor=None,
                 fillColor=colors.HexColor('#f8fafc'), strokeWidth=0))

    # 绘制同心圆网格（3圈：30%/60%/100%）
    for r_pct in [0.33, 0.66, 1.0]:
        d.add(Circle(cx, cy, radius * r_pct,
                     strokeColor=COLOR_GRID, fillColor=None, strokeWidth=1))

    # 绘制轴线
    for i in range(n):
        angle = 2 * math.pi * i / n - math.pi / 2
        x2 = cx + radius * math.cos(angle)
        y2 = cy + radius * math.sin(angle)
        d.add(Line(cx, cy, x2, y2, strokeColor=COLOR_GRID, strokeWidth=0.8))

    # 绘制数据多边形
    points = []
    for i, score in enumerate(scores):
        angle = 2 * math.pi * i / n - math.pi / 2
        r = radius * (score - 1) / 4  # 归一化 1-5 → 0-1
        x = cx + r * math.cos(angle)
        y = cy + r * math.sin(angle)
        points.extend([x, y])

    # 多边形填充（带渐变感）
    d.add(Polygon(points,
                  fillColor=colors.HexColor('#3b82f622'),
                  strokeColor=COLOR_EXCELLENT,
                  strokeWidth=2))

    # 绘制数据点圆圈（带颜色编码）
    for i, score in enumerate(scores):
        angle = 2 * math.pi * i / n - math.pi / 2
        r = radius * (score - 1) / 4
        x = cx + r * math.cos(angle)
        y = cy + r * math.sin(angle)
        dot_color = get_score_color(score)
        # 外圈白边
        d.add(Circle(x, y, 5, fillColor=dot_color, strokeColor=COLOR_WHITE, strokeWidth=2))
        # 小内圈高光
        d.add(Circle(x, y, 2.5, fillColor=colors.white, strokeColor=None))

    # 绘制维度标签（带分数标注）
    dim_labels = {
        'COG': '认知', 'TEC': '技术', 'COM': '表达',
        'SOC': '社交', 'ORG': '策划', 'PRS': '应变',
        'MGT': '管理', 'LLA': '学习'
    }
    for i, dim in enumerate(dims):
        angle = 2 * math.pi * i / n - math.pi / 2
        label_radius = radius + 14
        lx = cx + label_radius * math.cos(angle)
        ly = cy + label_radius * math.sin(angle)
        label = dim_labels.get(dim, dim)
        # 标签背景（让文字更清晰）
        s = String(lx, ly, label)
        s.fontName = 'Helvetica-Bold'
        s.fontSize = 9
        s.textAnchor = 'middle'
        s.fillColor = COLOR_EXCELLENT
        d.add(s)
        # 在标签下方加分数
        score_val = scores[i] if i < len(scores) else 3
        score_text = String(lx, ly - 11, f'{score_val:.1f}')
        score_text.fontName = 'Helvetica'
        score_text.fontSize = 7
        score_text.textAnchor = 'middle'
        score_text.fillColor = get_score_color(score_val)
        d.add(score_text)

    return d


# ============ V3 报告颜色常量（现代信息图表风格） ============
COLOR_V3_BLUE = colors.HexColor('#1e88e5')    # 主蓝
COLOR_V3_ORANGE = colors.HexColor('#ff9800')   # 辅橙
COLOR_V3_LIGHT_BLUE = colors.HexColor('#e3f2fd')  # 浅蓝背景
COLOR_V3_LIGHT_ORANGE = colors.HexColor('#fff3e0')  # 浅橙背景
COLOR_V3_WHITE = colors.white
COLOR_V3_DARK = colors.HexColor('#212121')     # 深灰文字
COLOR_V3_GRAY = colors.HexColor('#757575')    # 中灰文字
COLOR_V3_LIGHT_GRAY = colors.HexColor('#f5f5f5')  # 浅灰背景

def generate_pdf_48_v2(result_id, scores, answers, user_name, experience):
    """生成 V2 版 PDF 报告 — 现代信息图表风格（主蓝+辅橙配色）
    
    新增 answers 参数用于计算子能力分数，使"总结与优先行动"部分显示子能力而非维度。
    """
    buffer = io.BytesIO()

    if CHINESE_FONT:
        font_name = CHINESE_FONT
    else:
        raise Exception('中文字体不可用')

    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=12*mm, bottomMargin=12*mm,
                            leftMargin=12*mm, rightMargin=12*mm)
    styles = getSampleStyleSheet()

    # ============ V2/V3 共享常量 ============
    global COLOR_EXCELLENT, COLOR_GOOD, COLOR_IMPROVE
    global COLOR_BG_TOP, COLOR_BG_BOT, COLOR_BG_WHITE, COLOR_WHITE
    global COLOR_TEXT_DARK, COLOR_TEXT_MUTED, COLOR_GRID

    # V3 样式定义
    styles.add(ParagraphStyle(name='V3Cover', fontName=font_name, fontSize=28,
                               alignment=1, spaceAfter=4, textColor=COLOR_V3_BLUE))
    styles.add(ParagraphStyle(name='V3CoverSub', fontName=font_name, fontSize=14,
                               alignment=1, spaceAfter=2, textColor=COLOR_V3_GRAY))
    styles.add(ParagraphStyle(name='V3CoverInfo', fontName=font_name, fontSize=11,
                               alignment=1, spaceAfter=4, textColor=COLOR_V3_GRAY))
    styles.add(ParagraphStyle(name='V3Section', fontName=font_name, fontSize=14,
                               spaceAfter=8, spaceBefore=6, textColor=COLOR_V3_BLUE))
    styles.add(ParagraphStyle(name='V3CardTitle', fontName=font_name, fontSize=12,
                               spaceAfter=3, textColor=COLOR_V3_DARK))
    styles.add(ParagraphStyle(name='V3Text', fontName=font_name, fontSize=9,
                               spaceAfter=3, leading=13, textColor=COLOR_V3_DARK))
    styles.add(ParagraphStyle(name='V3Muted', fontName=font_name, fontSize=8.5,
                               spaceAfter=2, leading=12, textColor=COLOR_V3_GRAY))
    styles.add(ParagraphStyle(name='V3Footer', fontName=font_name, fontSize=8,
                               alignment=1, textColor=COLOR_V3_GRAY))
    styles.add(ParagraphStyle(name='V3Bullet', fontName=font_name, fontSize=9,
                               spaceAfter=2, leading=12, leftIndent=8,
                               textColor=COLOR_V3_DARK))
    styles.add(ParagraphStyle(name='V3Alert', fontName=font_name, fontSize=8,
                               spaceAfter=2, leading=11, textColor=COLOR_V3_GRAY))

    story = []

    # ============ 共享辅助函数 ============
    def make_card(content_list, bg_color, radius=6):
        inner = Table([[c] for c in content_list], colWidths=[78*mm])
        inner.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), bg_color),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('ROUNDEDCORNERS', [radius]),
            ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#e0e0e0')),
        ]))
        return inner

    def add_section_divider(story, color=COLOR_V3_BLUE):
        """装饰性分隔线"""
        divider_data = [['', '']]
        divider = Table(divider_data, colWidths=[40*mm, 130*mm], rowHeights=[2])
        divider.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, 0), color),
            ('BACKGROUND', (1, 0), (1, 0), colors.HexColor('#e0e0e0')),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        story.append(Spacer(1, 2*mm))
        story.append(divider)
        story.append(Spacer(1, 1*mm))

    def draw_circle_badge(score, dim_color):
        """绘制圆形分数徽章"""
        from reportlab.graphics.shapes import Drawing, Circle, String
        d = Drawing(50, 50)
        # 外圈
        d.add(Circle(25, 25, 22, fillColor=dim_color, strokeColor=None))
        # 内部白色
        d.add(Circle(25, 25, 18, fillColor=COLOR_V3_WHITE, strokeColor=None))
        # 分数文字
        score_str = f'{score:.1f}'
        d.add(String(25, 17, score_str, textAnchor='middle', fontSize=12,
                     fontName=font_name, fillColor=dim_color))
        return d

    # ========== 封面页：几何图形装饰 + 标题 ==========
    # 顶部装饰条
    top_bar = Table([['']], colWidths=[190*mm], rowHeights=[8])
    top_bar.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), COLOR_V3_BLUE),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    story.append(top_bar)

    # 装饰圆形
    from reportlab.graphics.shapes import Drawing, Circle
    deco = Drawing(190*mm, 30)
    deco.add(Circle(170, 15, 12, fillColor=COLOR_V3_ORANGE, strokeColor=None))
    deco.add(Circle(185, 8, 6, fillColor=COLOR_V3_BLUE, strokeColor=None))
    deco.add(Circle(15, 10, 8, fillColor=COLOR_V3_LIGHT_BLUE, strokeColor=None))
    story.append(deco)

    story.append(Spacer(1, 8*mm))
    story.append(Paragraph('8维能力深度评测报告', styles['V3Cover']))
    story.append(Paragraph(f'<b>{user_name}</b>', styles['V3CoverSub']))
    story.append(Paragraph(f'{experience} · {datetime.now().strftime("%Y年%m月%d日")}', styles['V3CoverInfo']))
    story.append(Spacer(1, 4*mm))

    # 维度数据准备
    dim_order = ['COG', 'TEC', 'COM', 'SOC', 'ORG', 'PRS', 'MGT', 'LLA']
    dim_names = {
        'COG': '认知能力', 'TEC': '技术掌握', 'COM': '理解表达',
        'SOC': '社交技能', 'ORG': '策划执行', 'PRS': '解决问题',
        'MGT': '管理技能', 'LLA': '持续学习'
    }
    dim_colors_map = {
        'COG': COLOR_V3_BLUE, 'TEC': COLOR_V3_ORANGE, 'COM': colors.HexColor('#26a69a'),
        'SOC': colors.HexColor('#7e57c2'), 'ORG': colors.HexColor('#ec407a'),
        'PRS': colors.HexColor('#ef5350'), 'MGT': colors.HexColor('#66bb6a'),
        'LLA': colors.HexColor('#5c6bc0')
    }

    sort_scores = sorted(scores.items(), key=lambda x: x[1]['average'], reverse=True)

    # ========== 综合评分摘要 ==========
    all_avgs = [s['average'] for _, s in sort_scores]
    total_avg = sum(all_avgs) / len(all_avgs)
    max_dim = sort_scores[0]
    min_dim = sort_scores[-1]

    # 综合评分卡片（横向4格）
    summary_data = [
        [Paragraph('<b>综合评分</b>', styles['V3Muted']),
         Paragraph(f'<b>{total_avg:.1f}</b>', styles['V3CardTitle']),
         Paragraph('<b>最突出</b>', styles['V3Muted']),
         Paragraph(f'<b>{dim_names.get(max_dim[0], max_dim[0])}</b><br/><font color="#1e88e5">{max_dim[1]["average"]:.1f}分</font>',
                  styles['V3Muted']),
         Paragraph('<b>待提升</b>', styles['V3Muted']),
         Paragraph(f'<b>{dim_names.get(min_dim[0], min_dim[0])}</b><br/><font color="#ff9800">{min_dim[1]["average"]:.1f}分</font>',
                  styles['V3Muted'])],
    ]
    summary_tbl = Table(summary_data, colWidths=[25*mm, 20*mm, 22*mm, 38*mm, 22*mm, 38*mm])
    summary_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), COLOR_V3_LIGHT_GRAY),
        ('FONTNAME', (0, 0), (-1, -1), font_name),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('ALIGN', (1, 0), (1, 0), 'CENTER'),
        ('ALIGN', (3, 0), (3, 0), 'CENTER'),
        ('ALIGN', (5, 0), (5, 0), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('BOX', (0, 0), (-1, -1), 1, COLOR_V3_BLUE),
    ]))
    story.append(summary_tbl)
    story.append(Spacer(1, 4*mm))

    # ========== 8维度圆形徽章展示 ==========
    story.append(Paragraph('维度得分一览', styles['V3Section']))

    # 绘制8个圆形徽章
    dim_badges = []
    for dim, data in sort_scores:
        badge = draw_circle_badge(data['average'], dim_colors_map.get(dim, COLOR_V3_BLUE))
        label = Paragraph(f'<b>{dim_names.get(dim, dim)}</b><br/>{data["average"]:.1f}', styles['V3Muted'])
        cell = Table([[badge], [label]], colWidths=[52*mm])
        cell.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ]))
        dim_badges.append(cell)

    # 4x2 网格布局
    badge_rows = []
    for i in range(0, 8, 4):
        row = dim_badges[i:i+4]
        while len(row) < 4:
            row.append(Spacer(1, 1))
        badge_rows.append(row)

    badge_table = Table(badge_rows, colWidths=[47*mm]*4)
    badge_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 2),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
    ]))
    story.append(badge_table)
    story.append(Spacer(1, 3*mm))
    add_section_divider(story, COLOR_V3_ORANGE)

    # ========== 核心优势 TOP 3（蓝色卡片） ==========
    story.append(Paragraph('★ 核心优势 TOP 3', styles['V3Section']))

    advantageDescV2 = {
        'COG': ('认知领跑者', '陈志明展现出极强的逻辑构建能力，尤其在处理非结构化信息时——能迅速剥离噪音，直达核心本质。',
                ['快速提炼复杂信息核心', '发现逻辑论证中的漏洞', '掌握新领域速度快于常人']),
        'TEC': ('技术适应力强', '陈志明对新技术保持开放心态，能快速上手并将新工具转化为生产力，是团队的数字化先锋。',
                ['AI和数据工具运用自如', '面对新技术快速上手', '遇问题自主排查解决']),
        'COM': ('沟通影响力强', '陈志明在信息传递场景中具有显著影响力——既能精准解读他人隐含意图，也能用简洁语言驱动团队决策。',
                ['准确理解对方言外之意', '复杂概念简洁化传达', '在会议中主导结论输出']),
        'SOC': ('高情商社交者', '陈志明善于在复杂人际网络中建立信任，尤其在跨部门协作场景中能有效协调分歧、促进共识。',
                ['敏锐感知团队情绪变化', '在冲突中促进各方达成共识', '与不同背景人士建立长期信任']),
        'ORG': ('高效执行者', '陈志明具备从目标到落地的完整策划执行能力，能在无外部监督下维持高标准，确保任务按时交付。',
                ['将模糊目标转化为清晰行动计划', '无人督促仍保持高效产出', '合理分配时间与资源']),
        'PRS': ('创新解决者', '陈志明擅长在压力下快速找到创新解法，原方案遇阻时能迅速切换视角，构建替代性解决方案。',
                ['原方案失败时迅速产出Plan B', '用结构化方法深挖问题根源', '无SOP时自创有效方案']),
        'MGT': ('团队赋能者', '陈志明具备项目与预期管理能力，能有效协调多方资源，在交付结果与上级期望之间建立清晰桥梁。',
                ['管理上下级期望落差', '多任务并行时准确判断优先级', '有效授权并建立跟进机制']),
        'LLA': ('持续成长者', '陈志明保持主动学习姿态，定期拓展知识边界并能从批评与挫折中提炼教训，职场成熟度提升速度高于同龄人。',
                ['定期阅读行业书刊、参加课程', '跨界探索本职以外新领域', '将批评转化为成长养分']),
    }

    top3 = sort_scores[:3]
    top3_cards = []
    for dim, s in top3:
        dim_label = dim_names.get(dim, dim)
        title, desc, traits = advantageDescV2.get(dim, (dim_label, '你最突出的能力领域。', []))
        card = [
            Paragraph(f'<b>{title}</b> <font color="#1e88e5" size="14">{s["average"]:.1f}分</font>',
                     styles['V3CardTitle']),
            Paragraph(desc, styles['V3Muted']),
        ]
        for t in traits[:3]:
            card.append(Paragraph(f'✓ {t}', styles['V3Bullet']))
        top3_cards.append(make_card(card, COLOR_V3_LIGHT_BLUE))

    # 横向3栏
    top3_row = Table([top3_cards], colWidths=[62*mm, 62*mm, 62*mm])
    top3_row.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 2),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    story.append(top3_row)
    story.append(Spacer(1, 3*mm))
    add_section_divider(story, COLOR_V3_ORANGE)

    # ========== 发展空间 BOTTOM 3（橙色卡片） ==========
    story.append(Paragraph('🌱 发展空间', styles['V3Section']))
    story.append(Paragraph('最具成长潜力的领域，建议重点关注：', styles['V3Muted']))

    developmentDescV2 = {
        'COG': ('认知能力', '在信息密集型岗位上更具竞争力的关键在于认知加工效率的进一步提升。',
                '用思维导图整理复杂问题结构；每日阅读后用3句话自测信息提炼准确度。'),
        'TEC': ('技术掌握', '补强数字化工具使用深度后，你将更自信地应对复杂技术环境，减少对团队的依赖。',
                '每周用2小时深入学习1个新工具，记录使用技巧到个人工具库。'),
        'COM': ('理解表达', '精进表达技巧后，你在跨部门协作、汇报和外部沟通场景中将更游刃有余。',
                '练习"电梯演讲"——30秒内说清一个复杂观点；写完报告后检查是否只需3句话总结。'),
        'SOC': ('社交技能', '提升人际敏锐度和冲突处理能力后，你将成为团队中不可或缺的协调枢纽。',
                '每周主动发起1次1对1交流；会议中观察每位发言者的情绪状态变化。'),
        'ORG': ('策划执行', '强化自我驱动的策划执行能力后，你的项目交付质量和时效性将显著提升。',
                '番茄工作法（25分钟专注+5分钟休息）；为每项任务设定比截止日期早1天的内部节点。'),
        'PRS': ('解决问题', '提升应变决策能力后，你将成为团队中不可替代的关键人物，能够在危机中带领团队找到破局点。',
                '每周针对1个业务难题绘制"逻辑树"（MECE原则）；练习"5 Whys 追问法"问到底。'),
        'MGT': ('管理技能', '精进管理技能后，你将更适合承担需要协调多方资源的复杂项目，成为团队信赖的桥梁。',
                '用SMART原则拆解每个目标；接受任务后第一时间复述理解并等待确认。'),
        'LLA': ('持续学习', '建立系统化的学习机制后，你的职业成长速度将显著快于同龄人，形成独特的专业壁垒。',
                '设定每月读完1本专业书籍的目标；建立个人知识库（笔记+标签系统）。'),
    }

    bot3 = sort_scores[-3:][::-1]
    dev_cards = []
    for dim, s in bot3:
        dim_label = dim_names.get(dim, dim)
        title = dim_label
        vals = developmentDescV2.get(dim, None)
        if vals:
            _, scenario, habit = vals
        else:
            scenario, habit = '建议优先投入提升资源。', ''
        card = [
            Paragraph(f'<b>{title}</b> <font color="#ff9800" size="14">{s["average"]:.1f}分</font>',
                     styles['V3CardTitle']),
            Paragraph(f'<i>{scenario}</i>', styles['V3Muted']),
            Paragraph(f'<b>行动</b>：{habit}', styles['V3Text']),
        ]
        dev_cards.append(make_card(card, COLOR_V3_LIGHT_ORANGE))

    dev_row = Table([dev_cards], colWidths=[62*mm, 62*mm, 62*mm])
    dev_row.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 2),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    story.append(dev_row)
    story.append(Spacer(1, 3*mm))
    add_section_divider(story, COLOR_V3_BLUE)

    # ========== 子能力细分详解 ==========
    story.append(PageBreak())
    story.append(Paragraph('子能力细分详解', styles['V3Section']))

    subInfoV2 = {
        'COG': [('资讯提炼', '从大量复杂信息中快速提取关键重点，忽略噪音，直达本质。'),
                ('逻辑推理', '面对矛盾资讯时进行理性分析，发现论证漏洞，做出合理判断。'),
                ('快速学习', '短时间内掌握全新技术领域，学习效率明显优于同侪平均水平。')],
        'TEC': [('数字生产力', '有效运用AI工具和数据分析工具提升个人和团队的工作效率。'),
                ('技术适应力', '面对新技术或新系统能快速上手，适应变化的能力强于常人。'),
                ('故障排查', '遇到技术问题时能自主排查根本原因，不依赖他人解决问题。')],
        'COM': [('解码能力', '准确理解对方言辞背后的真正意图，能处理含蓄和模糊的沟通。'),
                ('精炼表达', '用简洁清晰的语言表达复杂概念，书面和口头表达均逻辑清晰。'),
                ('口头影响力', '在公开发言和会议中能有效吸引听众注意力和影响决策。')],
        'SOC': [('情绪觉察', '敏锐感知他人情绪的细微变化，能根据对方状态调整沟通方式。'),
                ('冲突协调', '在团队分歧和人际冲突中能促进各方达成共识，保持冷静。'),
                ('关系建立', '与不同背景的人建立信任，维护长期人脉网络并保持有效联系。')],
        'ORG': [('目标规划', '将模糊目标拆解为清晰可衡量的行动步骤，制定详细计划和时间表。'),
                ('自主执行', '在无外部监督的情况下仍能维持高标准，主动推进任务不拖延。'),
                ('资源管理', '合理分配时间、人力、预算等资源，在有限条件下最大化产出。')],
        'PRS': [('应变能力', '原方案失败时能迅速调整策略，快速产出替代方案（Plan B）。'),
                ('根源分析', '用结构化方法（5 Whys、鱼骨图等）深挖问题根本原因。'),
                ('创新方案', '在无既有SOP的情况下能自行设计有效解决方案，常有创意突破。')],
        'MGT': [('预期管理', '有效管理上级和团队对任务结果的期望，避免目标与产出的落差。'),
                ('优先级取舍', '多任务并行时能准确判断轻重缓急，敢于拒绝次要任务的干扰。'),
                ('授权追踪', '有效分配任务并建立跟进机制，信任团队不过度干预执行过程。')],
        'LLA': [('知识更新', '保持定期阅读行业书刊、参加课程的习惯，主动更新专业知识体系。'),
                ('主动探索', '跨界探索本职以外的新领域，好奇心驱动学习，不带功利目的。'),
                ('挫折转化', '面对批评和失败能保持成长型心态，将负面反馈转化为改进养分。')],
    }

    for dim, s in sort_scores:
        subs = subInfoV2.get(dim, [])
        score_color = dim_colors_map.get(dim, COLOR_V3_BLUE)
        bar_width = int((s['average'] / 5.0) * 50)
        bar_table = Table([['']], colWidths=[bar_width*mm, (50-bar_width)*mm], rowHeights=[5])
        bar_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, 0), score_color),
            ('BACKGROUND', (1, 0), (1, 0), colors.HexColor('#e0e0e0')),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ]))

        dim_header = Table(
            [[Paragraph(f'<b>{dim_names.get(dim, dim)}</b> <font color="#1e88e5">{s["average"]:.1f}分</font>',
                       styles['V3CardTitle']), bar_table]],
            colWidths=[80*mm, 55*mm]
        )
        dim_header.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ]))
        story.append(dim_header)

        sub_rows = []
        for i in range(0, len(subs), 2):
            row = []
            for j in range(2):
                if i + j < len(subs):
                    sub_name, sub_desc = subs[i + j]
                    row.append(Paragraph(f'<b>· {sub_name}</b>：{sub_desc}', styles['V3Text']))
                else:
                    row.append(Spacer(1, 1))
            sub_rows.append(row)

        sub_table = Table(sub_rows, colWidths=[70*mm, 70*mm])
        sub_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ]))
        story.append(sub_table)
        story.append(Spacer(1, 2*mm))

    story.append(Spacer(1, 2*mm))
    add_section_divider(story, COLOR_V3_ORANGE)

    # ========== 总结与优先行动（子能力级别）==========
    story.append(PageBreak())
    story.append(Paragraph('总结与优先行动', styles['V3Section']))

    # 计算子能力分数
    sub_scores_all = calculate_sub_scores_48(answers)  # 已按分数升序排列
    # 取最低2个（需改进）和最高2个（需维持）
    need_improve = sub_scores_all[:2]  # 分数最低的2个
    need_maintain = sub_scores_all[-2:][::-1]  # 分数最高的2个（反转顺序）

    # 子能力中文名称映射
    sub_cn_names = {
        ('COG', 0): ('资讯提炼', '从大量复杂信息中快速提取关键重点，忽略噪音，直达本质。'),
        ('COG', 1): ('逻辑推理', '面对矛盾资讯时进行理性分析，发现论证漏洞，做出合理判断。'),
        ('COG', 2): ('快速学习', '短时间内掌握全新技术领域，学习效率明显优于同侪平均水平。'),
        ('TEC', 0): ('数字生产力', '有效运用AI工具和数据分析工具提升个人和团队的工作效率。'),
        ('TEC', 1): ('技术适应力', '面对新技术或新系统能快速上手，适应变化的能力强于常人。'),
        ('TEC', 2): ('故障排查', '遇到技术问题时能自主排查根本原因，不依赖他人解决问题。'),
        ('COM', 0): ('解码能力', '准确理解对方言辞背后的真正意图，能处理含蓄和模糊的沟通。'),
        ('COM', 1): ('精炼表达', '用简洁清晰的语言表达复杂概念，书面和口头表达均逻辑清晰。'),
        ('COM', 2): ('口头影响力', '在公开发言和会议中能有效吸引听众注意力和影响决策。'),
        ('SOC', 0): ('情绪觉察', '敏锐感知他人情绪的细微变化，能根据对方状态调整沟通方式。'),
        ('SOC', 1): ('冲突协调', '在团队分歧和人际冲突中能促进各方达成共识，保持冷静。'),
        ('SOC', 2): ('关系建立', '与不同背景的人建立信任，维护长期人脉网络并保持有效联系。'),
        ('ORG', 0): ('目标规划', '将模糊目标拆解为清晰可衡量的行动步骤，制定详细计划和时间表。'),
        ('ORG', 1): ('自主执行', '在无外部监督的情况下仍能维持高标准，主动推进任务不拖延。'),
        ('ORG', 2): ('资源管理', '合理分配时间、人力、预算等资源，在有限条件下最大化产出。'),
        ('PRS', 0): ('应变能力', '原方案失败时能迅速调整策略，快速产出替代方案（Plan B）。'),
        ('PRS', 1): ('根源分析', '用结构化方法（5 Whys、鱼骨图等）深挖问题根本原因。'),
        ('PRS', 2): ('创新方案', '在无既有SOP的情况下能自行设计有效解决方案，常有创意突破。'),
        ('MGT', 0): ('预期管理', '有效管理上级和团队对任务结果的期望，避免目标与产出的落差。'),
        ('MGT', 1): ('优先级取舍', '多任务并行时能准确判断轻重缓急，敢于拒绝次要任务的干扰。'),
        ('MGT', 2): ('授权追踪', '有效分配任务并建立跟进机制，信任团队不过度干预执行过程。'),
        ('LLA', 0): ('知识更新', '保持定期阅读行业书刊、参加课程的习惯，主动更新专业知识体系。'),
        ('LLA', 1): ('主动探索', '跨界探索本职以外的新领域，好奇心驱动学习，不带功利目的。'),
        ('LLA', 2): ('挫折转化', '面对批评和失败能保持成长型心态，将负面反馈转化为改进养分。'),
    }

    # 维度中文名称和图标
    dim_cn = {
        'COG': '认知能力', 'TEC': '技术掌握', 'COM': '理解表达',
        'SOC': '社交技能', 'ORG': '策划执行', 'PRS': '解决问题',
        'MGT': '管理技能', 'LLA': '持续学习'
    }
    dim_icon = {
        'COG': '🧠', 'TEC': '💻', 'COM': '💬',
        'SOC': '🤝', 'ORG': '🎯', 'PRS': '⚡',
        'MGT': '👥', 'LLA': '📚'
    }

    # ─── 需改进子能力（2个）──────────
    story.append(Paragraph('🔴 优先改进的子能力', styles['V3Section']))
    improve_cards = []
    for sub in need_improve:
        sub_key = (sub['dim'], sub['sub_idx'])
        sub_name, sub_desc = sub_cn_names.get(sub_key, (sub['name'], sub['desc']))
        dim_name = dim_cn.get(sub['dim'], sub['dim'])
        icon = dim_icon.get(sub['dim'], '📊')
        score_hex = '#ef4444' if sub['score'] < 2.5 else '#ff9800' if sub['score'] < 3.0 else '#757575'

        card = [
            Paragraph(f'<b>{icon} {sub_name}</b> <font color="{score_hex}" size="14">{sub["score"]:.1f}分</font>',
                     styles['V3CardTitle']),
            Paragraph(f'<b>所属维度：</b>{dim_name} | <b>等级：</b>{sub["level"]}', styles['V3Muted']),
            Paragraph(sub_desc, styles['V3Text']),
        ]
        improve_cards.append(make_card(card, COLOR_V3_LIGHT_ORANGE))

    # 2列布局
    improve_row = Table([[improve_cards[0], improve_cards[1]]], colWidths=[93*mm, 93*mm])
    improve_row.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    story.append(improve_row)
    story.append(Spacer(1, 3*mm))

    # ─── 需维持子能力（2个）──────────
    story.append(Paragraph('🟢 持续保持的子能力', styles['V3Section']))
    maintain_cards = []
    for sub in need_maintain:
        sub_key = (sub['dim'], sub['sub_idx'])
        sub_name, sub_desc = sub_cn_names.get(sub_key, (sub['name'], sub['desc']))
        dim_name = dim_cn.get(sub['dim'], sub['dim'])
        icon = dim_icon.get(sub['dim'], '📊')

        card = [
            Paragraph(f'<b>{icon} {sub_name}</b> <font color="#10b981" size="14">{sub["score"]:.1f}分</font>',
                     styles['V3CardTitle']),
            Paragraph(f'<b>所属维度：</b>{dim_name} | <b>等级：</b>{sub["level"]}', styles['V3Muted']),
            Paragraph(sub_desc + ' 继续保持这项优势！', styles['V3Text']),
        ]
        maintain_cards.append(make_card(card, COLOR_V3_LIGHT_BLUE))

    maintain_row = Table([[maintain_cards[0], maintain_cards[1]]], colWidths=[93*mm, 93*mm])
    maintain_row.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    story.append(maintain_row)
    story.append(Spacer(1, 3*mm))

    # 90天行动计划提示
    story.append(Spacer(1, 2*mm))
    plan_text = f'💡 <b>90天行动计划</b>：优先改进「{need_improve[0]["name"]}」和「{need_improve[1]["name"]}」，每30天完成1次自检并记录进步幅度。'
    story.append(Paragraph(plan_text, styles['V3Muted']))
    story.append(Spacer(1, 3*mm))
    add_section_divider(story, COLOR_V3_BLUE)

    # ========== 底部信息 ==========
    story.append(Paragraph(
        '本报告基于自评数据生成，结果仅供参考。自评可能受个人认知偏差影响，建议结合他人的客观反馈进行综合分析。'
        '如需一对一专业求职定位咨询，请联系 Santa Chow 教练获取个人化指导。',
        styles['V3Alert']))

    # 底部装饰条
    bottom_bar = Table([['']], colWidths=[190*mm], rowHeights=[4])
    bottom_bar.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), COLOR_V3_ORANGE),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    story.append(bottom_bar)

    doc.build(story)
    return buffer


def generate_pdf_48_v3(result_id, scores, answers, user_name, experience, font_name='Helvetica'):
    """
    生成PDF报告V3 - 数据可视化风格
    改进：
    1. 增加呼吸感（页边距从20mm增加到25mm）
    2. 数据可视化（水平柱状图、颜色编码评分条）
    3. 商业报告风格（专业配色、清晰视觉层级）
    
    参数：
    - font_name: 中文字体名称（由调用方传入，通常是 CHINESE_FONT）
    """
    buffer = io.BytesIO()
    
    # 页面设置 - 增加边距以提升呼吸感
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=25*mm,    # 增加上边距
        bottomMargin=25*mm,  # 增加下边距
        leftMargin=20*mm,    # 增加左边距
        rightMargin=20*mm     # 增加右边距
    )
    
    styles = {}
    
    # 自定义样式 - 改进排版（使用传入的 font_name）
    styles['V3Title'] = ParagraphStyle(
        name='V3Title',
        fontName=font_name,
        fontSize=24,
        textColor=COLOR_PRIMARY,
        alignment=TA_CENTER,
        spaceAfter=12,
        spaceBefore=6
    )
    
    styles['V3Subtitle'] = ParagraphStyle(
        name='V3Subtitle',
        fontName=font_name,
        fontSize=12,
        textColor=COLOR_TEXT_MID,
        alignment=TA_CENTER,
        spaceAfter=20
    )
    
    styles['V3Section'] = ParagraphStyle(
        name='V3Section',
        fontName=font_name,
        fontSize=16,
        textColor=COLOR_PRIMARY,
        spaceBefore=15,
        spaceAfter=10,
        backColor=COLOR_BG_LIGHT,
        borderPadding=8
    )
    
    styles['V3CardTitle'] = ParagraphStyle(
        name='V3CardTitle',
        fontName=font_name,
        fontSize=13,
        textColor=COLOR_PRIMARY,
        spaceAfter=6
    )
    
    styles['V3Text'] = ParagraphStyle(
        name='V3Text',
        fontName=font_name,
        fontSize=10,
        textColor=COLOR_TEXT_DARK,
        leading=14,
        spaceAfter=4
    )
    
    styles['V3Muted'] = ParagraphStyle(
        name='V3Muted',
        fontName=font_name,
        fontSize=9,
        textColor=COLOR_TEXT_MID,
        leading=12,
        spaceAfter=4
    )
    
    story = []
    
    # ========== 封面区 ==========
    story.append(Spacer(1, 20*mm))
    story.append(Paragraph('8维能力测评报告', styles['V3Title']))
    story.append(Paragraph(f'{user_name}  |  {experience}', styles['V3Subtitle']))
    story.append(Spacer(1, 10*mm))
    
    # ========== 水平柱状图（8维可视化） ==========
    dim_names = {
        'COG': '认知能力', 'TEC': '技术掌握', 'COM': '理解表达',
        'SOC': '社交技能', 'ORG': '策划执行', 'PRS': '解决问题',
        'MGT': '管理技能', 'LLA': '持续学习'
    }
    
    bar_chart = draw_horizontal_bar_chart(scores, dim_names, width=450, height=220)
    story.append(bar_chart)
    story.append(Spacer(1, 10*mm))
    
    # ========== 详细评分表（带颜色编码） ==========
    story.append(Paragraph('详细评分', styles['V3Section']))
    story.append(Spacer(1, 5*mm))
    
    # 排序分数
    sort_scores = sorted(scores.items(), key=lambda x: x[1]['average'], reverse=True)
    
    # 创建带颜色编码的表格
    table_data = [['维度', '分数', '等级', '可视化']]
    for dim, s in sort_scores:
        score_bar = draw_score_bar(s['average'], width=100, height=12)
        table_data.append([
            dim_names.get(dim, dim),
            f"{s['average']:.1f}",
            s['level'],
            score_bar
        ])
    
    score_table = Table(table_data, colWidths=[60*mm, 25*mm, 35*mm, 50*mm])
    score_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), COLOR_PRIMARY),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, -1), font_name),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('TOPPADDING', (0, 1), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, COLOR_BORDER),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, COLOR_BG_LIGHT]),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(score_table)
    story.append(Spacer(1, 10*mm))
    
    # ========== 核心优势 TOP 3 ==========
    story.append(Paragraph('★ 核心优势 TOP 3', styles['V3Section']))
    story.append(Spacer(1, 8*mm))
    
    advantageDescV3 = {
        'COG': ('认知领跑者',
                '展现出极强的逻辑构建能力，尤其在处理非结构化信息时——能迅速剥离噪音，直达核心本质。',
                ['快速提炼复杂信息核心', '发现逻辑论证中的漏洞', '掌握新领域速度快于常人']),
        'TEC': ('技术适应力强',
                '对新技术保持开放心态，能快速上手并将新工具转化为生产力，是团队的数字化先锋。',
                ['AI和数据工具运用自如', '面对新技术快速上手', '遇问题自主排查解决']),
        'COM': ('沟通影响力强',
                '在信息传递场景中具有显著影响力——既能精准解读他人隐含意图，也能用简洁语言驱动团队决策。',
                ['准确理解对方言外之意', '复杂概念简洁化传达', '在会议中主导结论输出']),
        'SOC': ('高情商社交者',
                '善于在复杂人际网络中建立信任，尤其在跨部门协作场景中能有效协调分歧、促进共识。',
                ['敏锐感知团队情绪变化', '在冲突中促进各方达成共识', '与不同背景人士建立长期信任']),
        'ORG': ('高效执行者',
                '具备从目标到落地的完整策划执行能力，能在无外部监督下维持高标准，确保任务按时交付。',
                ['将模糊目标转化为清晰行动计划', '无人督促仍保持高效产出', '合理分配时间与资源']),
        'PRS': ('创新解决者',
                '擅长在压力下快速找到创新解法，原方案遇阻时能迅速切换视角，构建替代性解决方案。',
                ['原方案失败时迅速产出Plan B', '用结构化方法深挖问题根源', '无SOP时自创有效方案']),
        'MGT': ('团队赋能者',
                '具备项目与预期管理能力，能有效协调多方资源，在交付结果与上级期望之间建立清晰桥梁。',
                ['管理上下级期望落差', '多任务并行时准确判断优先级', '有效授权并建立跟进机制']),
        'LLA': ('持续成长者',
                '保持主动学习姿态，定期拓展知识边界并能从批评与挫折中提炼教训，职场成熟度提升速度高于同龄人。',
                ['定期阅读行业书刊、参加课程', '跨界探索本职以外新领域', '将批评转化为成长养分']),
    }
    
    top3 = sort_scores[:3]
    top3_cards = []
    
    for dim, s in top3:
        dim_label = dim_names.get(dim, dim)
        title, desc, traits = advantageDescV3.get(dim, (dim_label, '你最突出的能力领域。', []))
        score_color = get_score_color_v3(s['average'])
        
        card_content = [
            Paragraph(f'<b>{title}</b>  <font color="{score_color.hexval()}">{s["average"]:.1f}分</font>',
                      styles['V3CardTitle']),
            Spacer(1, 3*mm),
            Paragraph(desc, styles['V3Muted']),
            Spacer(1, 3*mm),
        ]
        for t in traits[:3]:
            card_content.append(Paragraph(f'• {t}', styles['V3Text']))
        
        top3_cards.append(card_content)
    
    # 创建卡片
    def make_v3_card(content_list, bg_color=COLOR_BG_CARD):
        inner = Table([[c] for c in content_list], colWidths=[75*mm])
        inner.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), bg_color),
            ('LEFTPADDING', (0, 0), (-1, -1), 10),
            ('RIGHTPADDING', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('ROUNDEDCORNERS', [6]),
            ('LINEBELOW', (0, -1), (-1, -1), 0.5, COLOR_BORDER),
        ]))
        return inner
    
    card1 = make_v3_card(top3_cards[0], COLOR_ACCENT_GOOD.clone(alpha=0.1))
    card2 = make_v3_card(top3_cards[1], COLOR_ACCENT_GOOD.clone(alpha=0.1))
    card3 = make_v3_card(top3_cards[2], COLOR_ACCENT_GOOD.clone(alpha=0.1))
    
    cards_row = Table([[card1, card2, card3]], colWidths=[80*mm, 80*mm, 80*mm])
    cards_row.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    story.append(cards_row)
    story.append(Spacer(1, 10*mm))
    
    # ========== 发展空间 BOTTOM 3 ==========
    story.append(Paragraph('🌱 发展空间（最具成长潜力的领域）', styles['V3Section']))
    story.append(Spacer(1, 5*mm))
    story.append(Paragraph(
        '为了实现从"优秀"到"卓越"的跨越，建议重点关注以下领域：',
        styles['V3Text']
    ))
    story.append(Spacer(1, 5*mm))
    
    developmentDescV3 = {
        'COG': ('认知能力',
                '在信息密集型岗位上更具竞争力的关键在于认知加工效率的进一步提升。',
                '面对大量新信息时容易迷失重点，难以快速形成清晰结论。',
                '用思维导图整理复杂问题结构；每日阅读后用3句话自测信息提炼准确度。',
                '每周选取1篇深度文章，写出"一句话核心观点+3个支撑细节"'),
        'TEC': ('技术掌握',
                '补强数字化工具使用深度后，你将更自信地应对复杂技术环境，减少对团队的依赖。',
                '部分AI工具尚未深度使用；新系统上手需要比别人更长时间。',
                '每周用2小时深入学习1个新工具，记录使用技巧到个人工具库。',
                '遇到技术问题先自行排查30分钟，再决定是否求助'),
        'COM': ('理解表达',
                '精进表达技巧后，你在跨部门协作、汇报和外部沟通场景中将更游刃有余。',
                '在接受模糊指令时，未能第一时间反向对齐（Check-back）交付标准，导致后期返工。',
                '练习"电梯演讲"——30秒内说清一个复杂观点；写完报告后检查是否只需3句话总结。',
                '每次汇报后复盘"哪些信息听众记住了，哪些遗漏了"'),
        'SOC': ('社交技能',
                '提升人际敏锐度和冲突处理能力后，你将成为团队中不可或缺的协调枢纽。',
                '偶尔未能及时感知他人情绪；面对人际冲突倾向回避而非直面。',
                '每周主动发起1次1对1交流；会议中观察每位发言者的情绪状态变化。',
                '每次冲突后写"情绪复盘卡"——触发点、自己反应、改进点'),
        'ORG': ('策划执行',
                '强化自我驱动的策划执行能力后，你的项目交付质量和时效性将显著提升。',
                '无外部截止日期驱动时，容易陷入"等待完美时机"的拖延陷阱。',
                '番茄工作法（25分钟专注+5分钟休息）；为每项任务设定比截止日期早1天的内部节点。',
                '每周日规划下周TOP 3优先任务，完成后打勾确认'),
        'PRS': ('解决问题',
                '提升应变决策能力后，你将成为团队中不可替代的关键人物，能够在危机中带领团队找到破局点。',
                '当既定业务流程受到突发政策或技术限制阻断时，容易陷入局部细节纠缠，缺乏全局破局策略。',
                '每周针对1个业务难题绘制"逻辑树"（MECE原则）；练习"5 Whys 追问法"问到底。',
                '在项目总结中增加"意外应对机制"模块，刻意练习预案设计能力'),
        'MGT': ('管理技能',
                '精进管理技能后，你将更适合承担需要协调多方资源的复杂项目，成为团队信赖的桥梁。',
                '在接受模糊指令时，未能第一时间反向对齐（Check-back）交付标准，导致后期返工成本增加。',
                '用SMART原则拆解每个目标；接受任务后第一时间复述理解并等待确认。',
                '每周主动与上级对齐一次预期；练习"委托四步法"：说目标→给资源→少干预→做复盘'),
        'LLA': ('持续学习',
                '建立系统化的学习机制后，你的职业成长速度将显著快于同龄人，形成独特的专业壁垒。',
                '学习时间主要在工作需求驱动下发生，缺乏主动探索的规划性。',
                '设定每月读完1本专业书籍的目标；建立个人知识库（笔记+标签系统）。',
                '把每次批评写成"成长反馈卡"——事实→感受→教训→行动'),
    }
    
    bot3 = sort_scores[-3:][::-1]  # 倒数3个，反转顺序（最弱的在最后）
    dev_cards = []
    
    for dim, s in bot3:
        dim_label = dim_names.get(dim, dim)
        title = dim_label
        vals = developmentDescV3.get(dim, None)
        if vals:
            _, _, scenario, tool_tip, habit = vals
        else:
            scenario, tool_tip, habit = '建议优先投入提升资源。', '', ''
        
        score_color = get_score_color_v3(s['average'])
        
        card_content = [
            Paragraph(f'<b>{title}</b>  <font color="{score_color.hexval()}">{s["average"]:.1f}分</font>',
                      styles['V3CardTitle']),
            Spacer(1, 3*mm),
            Paragraph(f'<i>{scenario}</i>', styles['V3Muted']),
            Spacer(1, 2*mm),
            Paragraph(f'<b>思维工具</b>：{tool_tip}', styles['V3Text']),
            Paragraph(f'<b>复盘习惯</b>：{habit}', styles['V3Text']),
        ]
        dev_cards.append(card_content)
    
    # 创建发展卡片（暖色背景）
    def make_dev_card(content_list):
        inner = Table([[c] for c in content_list], colWidths=[75*mm])
        inner.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), COLOR_ACCENT_WARN.clone(alpha=0.1)),
            ('LEFTPADDING', (0, 0), (-1, -1), 10),
            ('RIGHTPADDING', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('ROUNDEDCORNERS', [6]),
            ('LINEBELOW', (0, -1), (-1, -1), 0.5, COLOR_BORDER),
        ]))
        return inner
    
    dev_card1 = make_dev_card(dev_cards[0])
    dev_card2 = make_dev_card(dev_cards[1])
    dev_card3 = make_dev_card(dev_cards[2])
    
    dev_cards_row = Table([[dev_card1, dev_card2, dev_card3]], colWidths=[80*mm, 80*mm, 80*mm])
    dev_cards_row.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    story.append(dev_cards_row)
    story.append(Spacer(1, 10*mm))
    
    # ========== 子能力详解 ==========
    story.append(Paragraph('子能力详解（8维×3项）', styles['V3Section']))
    story.append(Spacer(1, 5*mm))
    
    subInfo = {
        'COG': [
            ('信息提炼', '从大量复杂信息中快速识别核心要素，剥离无关噪音。'),
            ('逻辑推理', '基于给定前提进行严密的逻辑推导，发现论证中的漏洞。'),
            ('快速学习', '在新领域中迅速掌握核心概念和方法论，缩短学习曲线。'),
        ],
        'TEC': [
            ('数字工具', '熟练使用各类数字化工具和平台，提升工作效率。'),
            ('技术适应', '面对新技术和系统时能快速上手并转化为生产力。'),
            ('故障排查', '遇到技术问题时能自主排查并找到解决方案。'),
        ],
        'COM': [
            ('解码能力', '准确理解他人的言外之意和隐含需求。'),
            ('精炼表达', '用简洁清晰的语言传达复杂概念，避免信息过载。'),
            ('口头影响', '在会议和演讲中能有效引导讨论方向并促成决策。'),
        ],
        'SOC': [
            ('情绪觉察', '敏锐感知自己和他人的情绪变化，理解情绪背后的原因。'),
            ('冲突协调', '在团队分歧和人际冲突中能促进各方达成共识，保持冷静。'),
            ('关系建立', '与不同背景的人建立信任，维护长期人脉网络并保持有效联系。'),
        ],
        'ORG': [
            ('目标规划', '将模糊目标拆解为清晰可衡量的行动步骤，制定详细计划和时间表。'),
            ('自主执行', '在无外部监督的情况下仍能维持高标准，主动推进任务不拖延。'),
            ('资源管理', '合理分配时间、人力、预算等资源，在有限条件下最大化产出。'),
        ],
        'PRS': [
            ('应变能力', '原方案失败时能迅速调整策略，快速产出替代方案（Plan B）。'),
            ('根源分析', '用结构化方法（5 Whys、鱼骨图等）深挖问题根本原因。'),
            ('创新方案', '在无既有SOP的情况下能自行设计有效解决方案，常有创意突破。'),
        ],
        'MGT': [
            ('预期管理', '有效管理上级和团队对任务结果的期望，避免目标与产出的落差。'),
            ('优先级取舍', '多任务并行时能准确判断轻重缓急，敢于拒绝次要任务的干扰。'),
            ('授权追踪', '有效分配任务并建立跟进机制，信任团队不过度干预执行过程。'),
        ],
        'LLA': [
            ('知识更新', '保持定期阅读行业书刊、参加课程的习惯，主动更新专业知识体系。'),
            ('主动探索', '跨界探索本职以外的新领域，好奇心驱动学习，不带功利目的。'),
            ('挫折转化', '面对批评和失败能保持成长型心态，将负面反馈转化为改进养分。'),
        ],
    }
    
    for dim, s in sort_scores:
        subs = subInfo.get(dim, [])
        story.append(Paragraph(f'<b>{s["name"]}</b>（{s["average"]:.1f}分 · {s["level"]}）', styles['V3CardTitle']))
        for sub_name, sub_desc in subs:
            story.append(Paragraph(f'• <b>{sub_name}</b>：{sub_desc}', styles['V3Text']))
        story.append(Spacer(1, 5*mm))
    
    story.append(Spacer(1, 10*mm))
    
    # ========== 页脚 ==========
    story.append(Paragraph(
        f'Report ID: 8D-{result_id}  |  Santa Chow 8维能力评测系统  |  2026',
        styles['V3Muted']
    ))
    
    # 生成PDF
    doc.build(story)
    buffer.seek(0)
    return buffer

if __name__ == '__main__':
    # 测试代码（已禁用，避免 draw_horizontal_bar_chart 缺失导致崩溃）
    # 如需测试，请使用 Flask test_client 或直接调用 generate_pdf_48_v3() 函数
    print("提示：直接运行此文件已禁用PDF测试。使用 test_client 测试API，或运行Flask服务器。")
    import os
    port = int(os.environ.get('PORT', 5000))
    print(f"启动 Flask 服务器: http://0.0.0.0:{port}")
    app.run(debug=True, host='0.0.0.0', port=port, use_reloader=False)



def generate_pdf_48(result_id, scores, user_name, experience):
    """生成48题PDF报告（V1兼容版）"""
    buffer = io.BytesIO()

    if CHINESE_FONT:
        font_name = CHINESE_FONT
    else:
        raise Exception('中文字体不可用')

    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=18*mm, bottomMargin=18*mm)
    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(name='CTitle', fontName=font_name, fontSize=20, alignment=1, spaceAfter=8))
    styles.add(ParagraphStyle(name='CSub', fontName=font_name, fontSize=11, alignment=1, spaceAfter=16, textColor=colors.HexColor('#475569')))
    styles.add(ParagraphStyle(name='CText', fontName=font_name, fontSize=10, spaceAfter=6, leading=16))
    styles.add(ParagraphStyle(name='CSection', fontName=font_name, fontSize=13, spaceAfter=8, spaceBefore=10, textColor=colors.HexColor('#1e3a8a')))
    styles.add(ParagraphStyle(name='CSubsection', fontName=font_name, fontSize=11, spaceAfter=4, spaceBefore=6, textColor=colors.HexColor('#334155')))
    styles.add(ParagraphStyle(name='CFooter', fontName=font_name, fontSize=9, alignment=1, textColor=colors.HexColor('#94a3b8')))
    styles.add(ParagraphStyle(name='CAlert', fontName=font_name, fontSize=9, spaceAfter=4, leading=14, textColor=colors.HexColor('#92400e')))

    story = []

    # Title
    story.append(Paragraph('8维能力深度评测报告', styles['CTitle']))
    story.append(Paragraph(f'<b>{user_name}</b> | {experience} | 评测日期: {datetime.now().strftime("%Y-%m-%d")}', styles['CSub']))
    story.append(Spacer(1, 6*mm))

    # Dimension order
    dim_order = ['COG','TEC','COM','SOC','ORG','PRS','MGT','LLA']

    # Score table
    story.append(Paragraph('能力分数总览', styles['CSection']))
    data = [['维度', '分数', '等级']]
    sort_scores = sorted(scores.items(), key=lambda x: x[1]['average'], reverse=True)
    for dim, s in sort_scores:
        data.append([s['name'], f"{s['average']:.1f}", s['level']])

    table = Table(data, colWidths=[70*mm, 40*mm, 60*mm])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e3a8a')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, -1), font_name),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('TOPPADDING', (0, 1), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#ddd')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f0f4ff')]),
    ]))
    story.append(table)
    story.append(Spacer(1, 6*mm))

    # 核心优势
    advantageDesc = {
        'COG': ('你拥有出色的认知加工能力，能够高效处理复杂信息并形成清晰判断。',
                 ['面对大量信息能快速提炼核心', '逻辑推理严谨，能发现论证漏洞', '学习新领域速度快于常人']),
        'TEC': ('你具备较强的技术适应力，乐于拥抱新工具并能独立解决技术问题。',
                 ['AI和数据工具运用自如', '面对新技术能快速上手', '遇到问题能自主排查解决']),
        'COM': ('你擅长解读他人意图并有效表达自己，在沟通场景中具有影响力。',
                 ['能准确理解言外之意', '复杂概念能用简洁语言说清', '公开发言能影响团队决策']),
        'SOC': ('你对人际氛围高度敏感，善于在关系网络中建立信任与共识。',
                 ['能敏锐感知他人情绪变化', '擅长协调分歧、促进共识', '容易与不同背景的人建立信任']),
        'ORG': ('你具备从目标到落地的完整策划执行能力，能在无人监督下保持高标准。',
                 ['能将模糊目标拆解为可衡量步骤', '无人督促仍维持高产出', '善用资源最大化目标达成']),
        'PRS': ('你擅长在压力下快速找到创新解法，不被既有框架束缚。',
                 ['原方案失败能迅速提出Plan B', '用结构化方法深挖问题根源', '没有SOP时能自创有效方案']),
        'MGT': ('你具备项目与预期管理能力，能协调多方资源推动目标达成。',
                 ['能管理上下级的期望预期', '多任务并行时准确判断优先级', '能有效授权并建立跟进机制']),
        'LLA': ('你保持持续成长的学习姿态，主动拓展知识边界并能从挫折中提炼教训。',
                 ['定期阅读行业书刊、参加课程', '主动探索本职以外的领域', '能将批评和失败转化为成长养分']),
    }

    top3 = sort_scores[:3]
    story.append(Paragraph('核心优势（TOP 3）', styles['CSection']))
    for dim, s in top3:
        desc, traits = advantageDesc.get(dim, ('你最突出的能力领域。', []))
        story.append(Paragraph(f'<b>{s["name"]}</b> — {s["average"]:.1f}分', styles['CSubsection']))
        story.append(Paragraph(desc, styles['CText']))
        for t in traits:
            story.append(Paragraph(f'· {t}', styles['CText']))
        story.append(Spacer(1, 3*mm))
    story.append(Spacer(1, 3*mm))

    # 发展空间
    developmentDesc = {
        'COG': ('认知能力提升后，你在信息密集型岗位上将更具竞争力。',
                 ['信息多时容易迷失重点', '面对新领域需要较长时间适应', '复杂分析有时难以形成清晰结论'],
                 ['每天练习写100字资讯摘要', '用思维导图整理复杂问题结构', '阅读后强迫自己复述核心观点']),
        'TEC': ('技术能力补强后，你将更自信地应对数字化工作环境。',
                 ['部分AI工具尚未深度使用', '遇到技术问题倾向求助而非自行排查', '新系统上手需要比别人更长时间'],
                 ['每周用2小时深入学习一个新工具', '遇到问题先自行排查30分钟再求助', '建立个人工具库，记录使用技巧']),
        'COM': ('表达能力精进后，你在跨部门协作和汇报场景中将更游刃有余。',
                 ['有时难以用一句话说清复杂概念', '书面表达逻辑偶有跳跃', '在大型会议中影响力有限'],
                 ['练习"电梯演讲"：30秒说清一个观点', '写完报告后检查是否只需3句话总结', '主动争取主持小型会议的机会']),
        'SOC': ('社交敏锐度提升后，你在建立人脉网络和冲突处理上将更有优势。',
                 ['偶尔未能及时感知他人情绪', '面对人际冲突倾向回避', '人脉网络主要局限在工作范围内'],
                 ['每周主动发起1次1对1交流', '在会议中关注每位发言者的情绪状态', '维护人脉清单，每季度主动联系一次']),
        'ORG': ('策划执行能力强化后，你的项目交付质量和时效性将显著提升。',
                 ['有时计划赶不上变化', '没有外部截止日期时容易拖延', '多任务并行时偶有遗漏'],
                 ['每周日规划下周TOP 3优先任务', '使用番茄工作法（25分钟专注+5分钟休息）', '为每个任务设定比截止日期早1天的内部节点']),
        'PRS': ('解决问题能力提升后，你将成为团队中不可替代的关键人物。',
                 ['Plan A失败时需要较长时间切换', '有时只解决表面问题而非根本', '面对无先例的问题感到无从下手'],
                 ['遇到问题先连续问5个"为什么"', '练习写"问题分析备忘录"（现象→原因→方案）', '每解决一个问题，总结提炼成可复用的方法论']),
        'MGT': ('管理技能精进后，你将更适合承担需要协调多方资源的复杂项目。',
                 ['有时上级期望与实际产出有落差', '多任务并行时难以取舍', '授权后容易过度干预'],
                 ['用SMART原则拆解每个目标', '每周主动与上级对齐一次预期', '练习"委托四步法"：说目标→给资源→少干预→做复盘']),
        'LLA': ('持续学习能力强化后，你的职业成长速度将显著快于同龄人。',
                 ['学习时间主要在工作需求驱动下发生', '缺乏系统化的知识管理方法', '批评意见有时会带来情绪而非反思'],
                 ['设定每月读完1本专业书籍的目标', '建立个人知识库（笔记+标签系统）', '把每次批评写成"成长反馈卡"，提炼教训']),
    }

    bot3 = sort_scores[-3:][::-1]
    story.append(Paragraph('发展空间（最具成长潜力的领域）', styles['CSection']))
    for dim, s in bot3:
        desc, signs, actions = developmentDesc.get(dim, ('建议优先投入提升资源。', [], []))
        story.append(Paragraph(f'<b>{s["name"]}</b> — {s["average"]:.1f}分', styles['CSubsection']))
        story.append(Paragraph(desc, styles['CText']))
        story.append(Paragraph('常见表现：' + '；'.join(signs), styles['CText']))
        story.append(Paragraph('行动建议：' + '；'.join(actions), styles['CText']))
        story.append(Spacer(1, 3*mm))
    story.append(Spacer(1, 3*mm))

    # 子能力详解
    subInfo = {
        'COG': [
            ('资讯提炼', '从大量复杂信息中快速提取关键重点，忽略噪音，直达本质。'),
            ('逻辑推理', '面对矛盾资讯时进行理性分析，发现论证漏洞，做出合理判断。'),
            ('快速学习', '在短时间内掌握全新技术领域，学习效率明显优于同侪平均水平。'),
        ],
        'TEC': [
            ('数字生产力', '有效运用AI工具和数据分析工具提升个人和团队的工作效率。'),
            ('技术适应力', '面对新技术或新系统能快速上手，适应变化的能力强于常人。'),
            ('故障排查', '遇到技术问题时能自主排查根本原因，不依赖他人解决问题。'),
        ],
        'COM': [
            ('解码能力', '准确理解对方言辞背后的真正意图，能处理含蓄和模糊的沟通。'),
            ('精炼表达', '用简洁清晰的语言表达复杂概念，书面和口头表达均逻辑清晰。'),
            ('口头影响力', '在公开发言和会议中能有效吸引听众注意力和影响决策。'),
        ],
        'SOC': [
            ('情绪觉察', '敏锐感知他人情绪的细微变化，能根据对方状态调整沟通方式。'),
            ('冲突协调', '在团队分歧和人际冲突中能促进各方达成共识，保持冷静。'),
            ('关系建立', '与不同背景的人建立信任，维护长期人脉网络并保持有效联系。'),
        ],
        'ORG': [
            ('目标规划', '将模糊目标拆解为清晰可衡量的行动步骤，制定详细计划和时间表。'),
            ('自主执行', '在无外部监督的情况下仍能维持高标准，主动推进任务不拖延。'),
            ('资源管理', '合理分配时间、人力、预算等资源，在有限条件下最大化产出。'),
        ],
        'PRS': [
            ('应变能力', '原方案失败时能迅速调整策略，快速产出替代方案（Plan B）。'),
            ('根源分析', '用结构化方法（5 Whys、鱼骨图等）深挖问题根本原因。'),
            ('创新方案', '在无既有SOP的情况下能自行设计有效解决方案，常有创意突破。'),
        ],
        'MGT': [
            ('预期管理', '有效管理上级和团队对任务结果的期望，避免目标与产出的落差。'),
            ('优先级取舍', '多任务并行时能准确判断轻重缓急，敢于拒绝次要任务的干扰。'),
            ('授权追踪', '有效分配任务并建立跟进机制，信任团队不过度干预执行过程。'),
        ],
        'LLA': [
            ('知识更新', '保持定期阅读行业书刊、参加课程的习惯，主动更新专业知识体系。'),
            ('主动探索', '跨界探索本职以外的新领域，好奇心驱动学习，不带功利目的。'),
            ('挫折转化', '面对批评和失败能保持成长型心态，将负面反馈转化为改进养分。'),
        ],
    }

    story.append(Paragraph('子能力详解（8维×3项）', styles['CSection']))
    for dim, s in sort_scores:
        subs = subInfo.get(dim, [])
        story.append(Paragraph(f'<b>{s["name"]}</b>（{s["average"]:.1f}分 · {s["level"]}）', styles['CSubsection']))
        for sub_name, sub_desc in subs:
            story.append(Paragraph(f'· <b>{sub_name}</b>：{sub_desc}', styles['CText']))
        story.append(Spacer(1, 2*mm))
    story.append(Spacer(1, 8*mm))

    # Disclaimer
    story.append(Paragraph('本报告基于自评数据，仅供参考。如需一对一专业求职定位咨询，请联系 Santa Chow 教练获取个人化指导。', styles['CAlert']))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph(f'Report ID: 8D-{result_id} | Santa Chow 8维能力评测系统', styles['CFooter']))

    doc.build(story)
    buffer.seek(0)
    return buffer

# ============ 48题列表 ============
@app.route('/api/quiz/list_48')
@admin_required
def list_48():
    """列出48题提交记录（含来源Token）"""
    try:
        limit = int(request.args.get('limit', 100))
        with get_db() as conn:
            c = conn.cursor()
            c.execute('SELECT * FROM quiz_results_48 ORDER BY id DESC LIMIT ?', (limit,))
            rows = c.fetchall()
        return jsonify({'results': [dict(r) for r in rows], 'count': len(rows)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============ 管理接口 ============
@app.route('/api/admin/init_db', methods=['POST'])
@admin_required
def admin_init_db():
    """手动初始化数据库（创建所有表）"""
    init_db()
    with get_db() as conn:
        c = conn.cursor()
        if USE_POSTGRES:
            c.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
            tables = [r[0] for r in c.fetchall()]
        else:
            c.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r[0] for r in c.fetchall()]
    return jsonify({'success': True, 'tables': tables, 'db_type': get_db_type(), 'message': f'{get_db_type()} 数据库初始化完成'})

# ============ 数据库初始化（模块级别，gunicorn部署也可执行）============
# Railway使用gunicorn启动，在模块级别调用确保所有表在首次请求前创建
try:
    init_db()
    print(f"[DB] 数据库初始化完成: {get_db_type()} | {get_db_info()}")
except Exception as e:
    print(f"[DB] 数据库初始化警告: {e}")

# ============ V4 PDF 报告生成 ============

def generate_pdf_48_v4(result_id, scores, answers, user_name, experience, font_name='Helvetica'):
    """
    生成 8 维能力测评报告 V4
    设计：封面页 + 概览页 + 详细页，专业蓝绿配色。
    与 generate_pdf_48_v3 签名兼容，直接替换使用。
    新增 answers 参数用于计算子能力分数。
    """
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
    from reportlab.lib.units import mm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.graphics.shapes import Drawing, Rect, String
    from reportlab.graphics.charts.piecharts import Pie
    import io

    # 字体兜底：若无中文字体，改用 Helvetica（英文可显示）
    if not font_name:
        font_name = 'Helvetica'
        print('[PDF] 警告：无中文字体，PDF中文将显示异常')

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15*mm, rightMargin=15*mm,
        topMargin=15*mm, bottomMargin=15*mm
    )

    def ps(name, **kwargs):
        fn = kwargs.pop('fontName', font_name)  # 允许覆盖默认字体
        return ParagraphStyle(name, fontName=fn, **kwargs)

    story = []
    dim_names = {
        'COG': '认知能力', 'TEC': '技术能力', 'COM': '表达能力',
        'SOC': '社交能力', 'ORG': '策划执行', 'PRS': '解决问题',
        'MGT': '管理能力', 'LLA': '持续学习'
    }

    def score_color(s):
        if s >= 4.0: return COLOR_ACCENT_GOOD
        if s >= 3.0: return COLOR_SECONDARY
        if s >= 2.0: return COLOR_ACCENT_WARN
        return colors.HexColor('#ef4444')

    # ─── 封面页 ───
    story.append(Spacer(1, 28*mm))
    story.append(Paragraph("8 维 能 力 测 评 报 告", ps('CT', fontName=font_name+'-Bold',
        fontSize=26, textColor=COLOR_PRIMARY, alignment=TA_CENTER, spaceAfter=6)))
    story.append(Paragraph("8-Dimensional Competency Assessment Report",
        ps('CS', fontName=font_name, fontSize=11,
           textColor=COLOR_SECONDARY, alignment=TA_CENTER, spaceAfter=35)))

    info = [["姓名", user_name or '—'], ["经验", experience or '—'],
            ["报告ID", f"8D-{result_id}"], ["日期", datetime.now().strftime('%Y-%m-%d')]]
    t = Table(info, colWidths=[80, 210])
    t.setStyle(TableStyle([
        ('FONTNAME', (0,0), (0,-1), font_name+'-Bold'),
        ('FONTNAME', (1,0), (1,-1), font_name),
        ('FONTSIZE', (0,0), (-1,-1), 11),
        ('TEXTCOLOR', (0,0), (0,-1), COLOR_PRIMARY),
        ('TEXTCOLOR', (1,0), (1,-1), COLOR_TEXT_DARK),
        ('TOPPADDING', (0,0), (-1,-1), 9), ('BOTTOMPADDING', (0,0), (-1,-1), 9),
        ('LINEBELOW', (0,-1), (-1,-1), 1, COLOR_BORDER),
    ]))
    story.append(t)
    story.append(Spacer(1, 25*mm))
    bar = Table([[""]], colWidths=[290], rowHeights=[50])
    bar.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), COLOR_PRIMARY),
                              ('TOPPADDING', (0,0), (-1,-1), 0),
                              ('BOTTOMPADDING', (0,0), (-1,-1), 0)]))
    story.append(bar)
    story.append(PageBreak())

    # ─── 概览页 ───
    # 综合评分块
    all_scores = [v['average'] for v in scores.values()] if scores else []
    avg = sum(all_scores) / len(all_scores) if all_scores else 0
    label = "优秀" if avg >= 4.0 else "良好" if avg >= 3.0 else "中等"

    sb = Table([
        [Paragraph(f"<b>{avg:.1f}</b>", ps('BS', fontName=font_name+'-Bold',
            fontSize=52, textColor=score_color(avg), alignment=TA_CENTER, leading=56))],
        [Paragraph(label, ps('SL', fontSize=15, textColor=COLOR_TEXT_MID, alignment=TA_CENTER))],
        [Paragraph("综合评分", ps('SS', fontSize=10, textColor=COLOR_TEXT_MID, alignment=TA_CENTER))],
    ], colWidths=[170])
    sb.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), COLOR_BG_LIGHT),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('TOPPADDING', (0,0), (-1,-1), 10), ('BOTTOMPADDING', (0,0), (-1,-1), 10)]))

    # 优/弱分布
    top3 = sorted(scores.items(), key=lambda x: x[1]['average'], reverse=True)[:3]
    bot3 = sorted(scores.items(), key=lambda x: x[1]['average'])[:3]
    db = Table([["优势维度", "待提升维度"], [f"TOP {len(top3)}", f"BOTTOM {len(bot3)}"]],
               colWidths=[85, 85])
    db.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (0,-1), COLOR_ACCENT_GOOD),
        ('BACKGROUND', (1,0), (1,-1), COLOR_ACCENT_WARN),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,-1), font_name+'-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 9), ('FONTSIZE', (0,1), (-1,1), 18),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('TOPPADDING', (0,0), (-1,-1), 10), ('BOTTOMPADDING', (0,0), (-1,-1), 10),
    ]))

    ov = Table([[sb, db]], colWidths=['50%', '50%'])
    ov.setStyle(TableStyle([('ALIGN', (0,0), (-1,-1), 'CENTER'),
                            ('VALIGN', (0,0), (-1,-1), 'MIDDLE')]))
    story.append(ov)
    story.append(Spacer(1, 6*mm))

    # 8维柱状图
    if scores:
        bh = 22; gap = 7; lw = 85; sw = 35
        maxw = 440 - lw - sw - 15
        ch = Drawing(440, len(scores)*(bh+gap)+5)
        sorted_s = sorted(scores.items(), key=lambda x: x[1]['average'], reverse=True)
        for i, (dim, s) in enumerate(sorted_s):
            y = len(scores)*(bh+gap) - (i+1)*(bh+gap)
            ch.add(String(5, y+bh/2, dim_names.get(dim, dim),
                          fontSize=9, fillColor=COLOR_TEXT_DARK))
            bw = (s['average']/5.0)*maxw
            ch.add(Rect(lw, y, bw, bh, fillColor=score_color(s['average']), strokeColor=None))
            ch.add(String(lw+bw+4, y+bh/2, f"{s['average']:.1f}",
                          fontSize=9, fillColor=COLOR_TEXT_DARK))
        story.append(ch)

    story.append(PageBreak())

    # ─── TOP 3 优势 ───
    story.append(Paragraph("核心优势 TOP 3", ps('TT', fontName=font_name+'-Bold',
        fontSize=15, textColor=COLOR_ACCENT_GOOD, spaceAfter=10)))
    for i, (dim, s) in enumerate(top3):
        desc = s.get('description', '')
        ct = Table([[Paragraph(f"<b>TOP {i+1}  {dim_names.get(dim, dim)}</b>",
                               ps('ct', fontName=font_name+'-Bold', fontSize=12,
                                  textColor=COLOR_ACCENT_GOOD)),
                     Paragraph(f"<b>{s['average']:.1f}</b>/5.0",
                               ps('cs', fontName=font_name+'-Bold', fontSize=13,
                                  textColor=COLOR_ACCENT_GOOD, alignment=TA_RIGHT))],
                    [Paragraph(desc, ps('cd', fontName=font_name, fontSize=10,
                                        textColor=COLOR_TEXT_DARK)), ""]],
                   colWidths=[340, 100])
        ct.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), COLOR_BG_LIGHT),
            ('TOPPADDING', (0,0), (-1,-1), 10), ('BOTTOMPADDING', (0,0), (-1,-1), 10),
            ('LEFTPADDING', (0,0), (-1,-1), 12), ('RIGHTPADDING', (0,0), (-1,-1), 12),
            ('LINEBELOW', (0,0), (-1,0), 1.5, COLOR_ACCENT_GOOD),
            ('SPAN', (0,1), (1,1)),
        ]))
        story.append(ct); story.append(Spacer(1, 5))

    story.append(Spacer(1, 5*mm))

    # ─── BOTTOM 3 发展区 ───
    story.append(Paragraph("待提升领域", ps('DT', fontName=font_name+'-Bold',
        fontSize=15, textColor=COLOR_ACCENT_WARN, spaceAfter=10)))
    for i, (dim, s) in enumerate(bot3):
        desc = s.get('description', '')
        ct = Table([[Paragraph(f"<b>{dim_names.get(dim, dim)}</b>",
                               ps('dt', fontName=font_name+'-Bold', fontSize=12,
                                  textColor=COLOR_ACCENT_WARN)),
                     Paragraph(f"<b>{s['average']:.1f}</b>/5.0",
                               ps('ds', fontName=font_name+'-Bold', fontSize=13,
                                  textColor=COLOR_ACCENT_WARN, alignment=TA_RIGHT))],
                    [Paragraph(desc, ps('dd', fontName=font_name, fontSize=10,
                                        textColor=COLOR_TEXT_DARK)), ""]],
                   colWidths=[340, 100])
        ct.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#fffbeb')),
            ('TOPPADDING', (0,0), (-1,-1), 10), ('BOTTOMPADDING', (0,0), (-1,-1), 10),
            ('LEFTPADDING', (0,0), (-1,-1), 12), ('RIGHTPADDING', (0,0), (-1,-1), 12),
            ('LINEBELOW', (0,0), (-1,0), 1.5, COLOR_ACCENT_WARN),
            ('SPAN', (0,1), (1,1)),
        ]))
        story.append(ct); story.append(Spacer(1, 5))

    story.append(PageBreak())

    # ─── 人格画像报告 ───
    analysis = generate_personality_analysis(scores)
    profile = analysis.get('profile', {})
    tension  = analysis.get('tension_analysis', {})
    defects  = analysis.get('defect_reshaping', {})

    # 人格画像标题
    story.append(Paragraph("人格画像报告", ps('PT', fontName=font_name+'-Bold',
        fontSize=17, textColor=COLOR_PRIMARY, spaceAfter=6)))
    story.append(Paragraph("Personality Profile & Behavioral Archetype Analysis",
        ps('PS', fontName=font_name, fontSize=9,
           textColor=COLOR_SECONDARY, spaceAfter=12)))
    story.append(Spacer(1, 3*mm))

    # 人格类型卡
    type_color = COLOR_SECONDARY
    type_bg   = colors.HexColor('#eff6ff')
    type_card = Table([[
        Paragraph(f"<b>{profile.get('type', '—')}</b>",
                  ps('pc1', fontName=font_name+'-Bold', fontSize=22,
                     textColor=type_color, alignment=TA_CENTER)),
        Paragraph(f"<b>{profile.get('sub_type', '—')}</b><br/>"
                  f"<font size=9 color='#{COLOR_TEXT_MID.hexval()[2:]}'>{profile.get('third_type', '')}</font>",
                  ps('pc2', fontName=font_name+'-Bold', fontSize=13,
                     textColor=COLOR_TEXT_DARK, alignment=TA_CENTER, leading=18)),
        Paragraph(f"<b>{profile.get('overall_score', '—')}</b>/5.0<br/>"
                  f"<font size=8 color='#{COLOR_TEXT_MID.hexval()[2:]}'>综合评分</font>",
                  ps('pc3', fontName=font_name+'-Bold', fontSize=16,
                     textColor=score_color(profile.get('overall_score', 0)),
                     alignment=TA_CENTER, leading=18)),
    ]], colWidths=[105, 175, 160])
    type_card.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), type_bg),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 14), ('BOTTOMPADDING', (0,0), (-1,-1), 14),
        ('LINEAFTER', (0,0), (1,0), 1, COLOR_BORDER),
    ]))
    story.append(type_card)
    story.append(Spacer(1, 5*mm))

    # 维度行为标签（8宫格）
    dim_lbls = profile.get('dimension_labels', {})
    tags_row1 = [
        f"🤔 {dim_lbls.get('COG','—')} · {dim_lbls.get('TEC','—')} · {dim_lbls.get('COM','—')}",
        f"🤝 {dim_lbls.get('SOC','—')} · {dim_lbls.get('MGT','—')} · {dim_lbls.get('LLA','—')}",
    ]
    tags_row2 = [
        f"🎯 {dim_lbls.get('ORG','—')} · {dim_lbls.get('PRS','—')}",
        f"综合优势：{profile.get('top_dimension','—')}  |  成长区：{profile.get('growth_dimension','—')}",
    ]
    dim_tbl = Table([
        [Paragraph(tags_row1[0], ps('tb1', fontName=font_name, fontSize=9,
                                    textColor=COLOR_TEXT_DARK)),
         Paragraph(tags_row1[1], ps('tb2', fontName=font_name, fontSize=9,
                                    textColor=COLOR_TEXT_DARK))],
        [Paragraph(tags_row2[0], ps('tb3', fontName=font_name, fontSize=9,
                                    textColor=COLOR_TEXT_DARK)),
         Paragraph(tags_row2[1], ps('tb4', fontName=font_name, fontSize=9,
                                    textColor=COLOR_TEXT_DARK))],
    ], colWidths=[220, 220])
    dim_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), COLOR_BG_LIGHT),
        ('TOPPADDING', (0,0), (-1,-1), 7), ('BOTTOMPADDING', (0,0), (-1,-1), 7),
        ('LEFTPADDING', (0,0), (-1,-1), 10), ('RIGHTPADDING', (0,0), (-1,-1), 10),
        ('LINEBELOW', (0,-1), (-1,-1), 0.5, COLOR_BORDER),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(dim_tbl)
    story.append(Spacer(1, 5*mm))

    # 行为特征
    traits = profile.get('traits', [])
    story.append(Paragraph("<b>行为特征</b>", ps('bfh', fontName=font_name+'-Bold',
        fontSize=10, textColor=COLOR_PRIMARY, spaceAfter=5)))
    for t in traits[:5]:
        row = Table([[Paragraph(f"• {t}", ps('trow', fontName=font_name,
            fontSize=9, textColor=COLOR_TEXT_DARK, leading=14))]],
            colWidths=[440])
        row.setStyle(TableStyle([
            ('TOPPADDING', (0,0), (-1,-1), 3), ('BOTTOMPADDING', (0,0), (-1,-1), 3),
            ('LEFTPADDING', (0,0), (-1,-1), 8),
        ]))
        story.append(row)

    story.append(PageBreak())

    # ─── 张力分析报告 ───
    t_items = tension.get('items', [])
    t_level = tension.get('level', '低危')
    t_score = tension.get('score', 0)

    story.append(Paragraph("张力分析报告", ps('TT2', fontName=font_name+'-Bold',
        fontSize=17, textColor=colors.HexColor('#7c3aed'), spaceAfter=6)))
    story.append(Paragraph("Internal Tension & Conflict Pattern Analysis",
        ps('TS', fontName=font_name, fontSize=9,
           textColor=colors.HexColor('#8b5cf6'), spaceAfter=8)))

    # 张力概览卡
    level_colors = {'高危': colors.HexColor('#ef4444'), '中危': colors.HexColor('#f59e0b'), '低危': colors.HexColor('#10b981')}
    lc = level_colors.get(t_level, colors.HexColor('#64748b'))
    overview_card = Table([[
        Paragraph(f"<b>{t_score}%</b>", ps('ovs', fontName=font_name+'-Bold',
            fontSize=36, textColor=lc, alignment=TA_CENTER, leading=40)),
        Table([
            [Paragraph("<b>张力指数</b>", ps('ovh1', fontName=font_name+'-Bold',
                fontSize=10, textColor=COLOR_TEXT_DARK)),
             Paragraph("<b>张力等级</b>", ps('ovh2', fontName=font_name+'-Bold',
                fontSize=10, textColor=COLOR_TEXT_DARK))],
            [Paragraph(t_level, ps('ovv1', fontName=font_name+'-Bold',
                fontSize=14, textColor=lc)),
             Paragraph(f"<b>{len(t_items)}</b> 项核心张力",
                ps('ovv2', fontName=font_name, fontSize=10, textColor=COLOR_TEXT_DARK))],
        ], colWidths=[110, 150]),
    ]], colWidths=[120, 260])
    overview_card.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#faf5ff')),
        ('ALIGN', (0,0), (0,0), 'CENTER'), ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 12), ('BOTTOMPADDING', (0,0), (-1,-1), 12),
        ('LEFTPADDING', (0,0), (-1,-1), 15), ('LINEAFTER', (0,0), (0,0), 1, COLOR_BORDER),
    ]))
    story.append(overview_card)
    story.append(Spacer(1, 5*mm))

    # 各张力项
    tension_colors_map = {
        'tech-vs-social': colors.HexColor('#3b82f6'),
        'social-vs-tech': colors.HexColor('#06b6d4'),
        'plan-vs-execute': colors.HexColor('#8b5cf6'),
        'solve-vs-plan': colors.HexColor('#ec4899'),
        'social-vs-mgmt': colors.HexColor('#f59e0b'),
        'mgmt-vs-social': colors.HexColor('#f97316'),
        'tech-vs-stagnate': colors.HexColor('#ef4444'),
        'learn-vs-apply': colors.HexColor('#84cc16'),
        'express-vs-think': colors.HexColor('#06b6d4'),
        'think-vs-express': colors.HexColor('#14b8a6'),
        'solo-expert': colors.HexColor('#f43f5e'),
        'drift-risk': colors.HexColor('#dc2626'),
        'burnout-risk': colors.HexColor('#b91c1c'),
    }
    for i, t in enumerate(t_items):
        tc = tension_colors_map.get(t.get('type', ''), COLOR_SECONDARY)
        tcard = Table([
            [Paragraph(f"<b>{i+1}. {t.get('headline', '')}</b>",
                      ps(f't{i}a', fontName=font_name+'-Bold', fontSize=11,
                         textColor=tc)),
             Paragraph(f"<b>张力系数：{20 if i==0 else 15}%</b>",
                      ps(f't{i}b', fontName=font_name+'-Bold', fontSize=9,
                         textColor=tc, alignment=TA_RIGHT))],
            [Paragraph(t.get('detail', ''),
                      ps(f't{i}c', fontName=font_name, fontSize=9,
                         textColor=COLOR_TEXT_DARK, leading=14)), ""],
        ], colWidths=[360, 80])
        tcard.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#faf5ff') if i%2==0 else colors.HexColor('#f5f3ff')),
            ('TOPPADDING', (0,0), (-1,-1), 8), ('BOTTOMPADDING', (0,0), (-1,-1), 8),
            ('LEFTPADDING', (0,0), (-1,-1), 12), ('RIGHTPADDING', (0,0), (-1,-1), 12),
            ('LINEBELOW', (0,0), (-1,0), 1.5, tc),
            ('SPAN', (0,1), (1,1)),
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ]))
        story.append(tcard); story.append(Spacer(1, 4))

    story.append(PageBreak())

    # ─── 缺陷重塑报告 ───
    story.append(Paragraph("缺陷重塑报告", ps('DR', fontName=font_name+'-Bold',
        fontSize=17, textColor=colors.HexColor('#dc2626'), spaceAfter=6)))
    story.append(Paragraph("Competency Gap & Targeted Development Plan",
        ps('DS', fontName=font_name, fontSize=9,
           textColor=colors.HexColor('#f87171'), spaceAfter=8)))

    areas = defects.get('areas', [])
    for i, area in enumerate(areas):
        d_score = area.get('score', 0)
        d_name  = area.get('name', '')
        d_en    = area.get('en', '')
        d_sign  = area.get('sign', '')
        d_action = area.get('action', '')
        d_resource = area.get('resource', '')
        priority_label = '🔴 最高优先级' if i == 0 else f'🟡 优先级 {i+1}'
        dcard = Table([
            [Paragraph(f"<b>{priority_label} · {d_name}</b> ({d_en})",
                      ps(f'd{i}a', fontName=font_name+'-Bold', fontSize=11,
                         textColor=colors.HexColor('#dc2626'))),
             Paragraph(f"<b>{d_score:.1f}</b>/5.0",
                      ps(f'd{i}b', fontName=font_name+'-Bold', fontSize=13,
                         textColor=colors.HexColor('#dc2626'), alignment=TA_RIGHT))],
            [Paragraph(f"<b>⚠️ {d_sign}</b>",
                      ps(f'd{i}c', fontName=font_name+'-Bold', fontSize=9,
                         textColor=COLOR_TEXT_DARK, leading=13)), ""],
            [Paragraph(f"<b>✅ 行动方案：</b>{d_action}",
                      ps(f'd{i}d', fontName=font_name, fontSize=9,
                         textColor=COLOR_TEXT_DARK, leading=13)), ""],
            [Paragraph(f"<b>📚 推荐资源：</b>{d_resource}",
                      ps(f'd{i}e', fontName=font_name, fontSize=9,
                         textColor=COLOR_TEXT_MID, leading=13)), ""],
        ], colWidths=[370, 70])
        dcard.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#fff5f5') if i==0 else colors.HexColor('#fef2f2')),
            ('TOPPADDING', (0,0), (-1,-1), 8), ('BOTTOMPADDING', (0,0), (-1,-1), 8),
            ('LEFTPADDING', (0,0), (-1,-1), 12), ('RIGHTPADDING', (0,0), (-1,-1), 12),
            ('LINEBELOW', (0,0), (-1,0), 2, colors.HexColor('#dc2626')),
            ('SPAN', (0,1), (1,1)), ('SPAN', (0,2), (1,2)), ('SPAN', (0,3), (1,3)),
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ]))
        story.append(dcard); story.append(Spacer(1, 5))

    # 综合结论
    conclusion = analysis.get('conclusion', '')
    if conclusion:
        story.append(Spacer(1, 4*mm))
        conc_tbl = Table([[Paragraph(f"<b>📌 综合发展建议：</b>{conclusion}",
            ps('conc', fontName=font_name, fontSize=9,
               textColor=COLOR_TEXT_DARK, leading=14))]],
            colWidths=[440])
        conc_tbl.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#eff6ff')),
            ('TOPPADDING', (0,0), (-1,-1), 10), ('BOTTOMPADDING', (0,0), (-1,-1), 10),
            ('LEFTPADDING', (0,0), (-1,-1), 12), ('RIGHTPADDING', (0,0), (-1,-1), 12),
        ]))
        story.append(conc_tbl)

    story.append(PageBreak())

    # ─── 各维度详细 ───
    story.append(Paragraph("各维度能力详解", ps('LTT', fontName=font_name+'-Bold',
        fontSize=15, textColor=COLOR_PRIMARY, spaceAfter=10)))

    sub_names = {
        'COG': ['信息提炼','逻辑推理','快速学习'],
        'TEC': ['数字生产力','技术适应力','故障排查'],
        'COM': ['解码能力','精炼表达','口头影响力'],
        'SOC': ['情绪觉察','冲突协调','关系建立'],
        'ORG': ['目标规划','高标准执行','资源管理'],
        'PRS': ['Plan B产出','根源分析','创新方案'],
        'MGT': ['任务预期管理','优先级取舍','授权追踪'],
        'LLA': ['知识更新','主动探索','挫折转化'],
    }

    for dim, s in scores.items():
        subs = s.get('sub_abilities', [])
        rows = [[Paragraph(f"<b>{dim_names.get(dim, dim)}</b>",
                           ps('dn', fontName=font_name+'-Bold', fontSize=11,
                              textColor=COLOR_PRIMARY)),
                 Paragraph(f"{s['average']:.1f}/5",
                           ps('ds2', fontName=font_name+'-Bold', fontSize=11,
                              textColor=score_color(s['average']), alignment=TA_RIGHT))]]
        for j, sub in enumerate(subs):
            rows.append([Paragraph(sub_names.get(dim, ['—']*3)[j] if j < len(sub_names.get(dim, [])) else '—',
                                   ps('sl', fontName=font_name, fontSize=9,
                                      textColor=COLOR_TEXT_MID, leading=12)),
                         Paragraph(f"{sub.get('score', 0):.1f}",
                                   ps('ss2', fontName=font_name, fontSize=9,
                                      textColor=COLOR_TEXT_DARK, alignment=TA_RIGHT))])
        t = Table(rows, colWidths=[390, 50])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), COLOR_BG_LIGHT),
            ('BACKGROUND', (0,1), (-1,-1), COLOR_BG_CARD),
            ('TOPPADDING', (0,0), (-1,-1), 6), ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ('LEFTPADDING', (0,0), (-1,-1), 8), ('RIGHTPADDING', (0,0), (-1,-1), 8),
            ('LINEBELOW', (0,0), (-1,0), 1, COLOR_PRIMARY),
            ('LINEBELOW', (0,-1), (-1,-1), 0.5, COLOR_BORDER),
        ]))
        story.append(t); story.append(Spacer(1, 3))

    # ─── 总结与行动（子能力级别）──────────
    story.append(PageBreak())
    story.append(Paragraph("总结与行动", ps('SAT', fontName=font_name+'-Bold',
        fontSize=15, textColor=COLOR_PRIMARY, spaceAfter=10)))
    story.append(Paragraph("基于你的评测结果，建议按以下优先级采取行动：",
        ps('SATDesc', fontName=font_name, fontSize=9, textColor=COLOR_TEXT_MID, spaceAfter=8)))

    # 计算子能力分数
    sub_scores_all = calculate_sub_scores_48(answers)  # 已按分数升序排列
    # 取最低2个（需改进）和最高2个（需维持）
    need_improve = sub_scores_all[:2]  # 分数最低的2个
    need_maintain = sub_scores_all[-2:][::-1]  # 分数最高的2个（反转顺序）
    
    # 子能力中文名称映射
    sub_cn_names = {
        ('COG', 0): ('资讯提炼', '从大量复杂信息中快速提取关键重点，忽略噪音，直达本质。'),
        ('COG', 1): ('逻辑推理', '面对矛盾资讯时进行理性分析，发现论证漏洞，做出合理判断。'),
        ('COG', 2): ('快速学习', '短时间内掌握全新技术领域，学习效率明显优于同侪平均水平。'),
        ('TEC', 0): ('数字生产力', '有效运用AI工具和数据分析工具提升个人和团队的工作效率。'),
        ('TEC', 1): ('技术适应力', '面对新技术或新系统能快速上手，适应变化的能力强于常人。'),
        ('TEC', 2): ('故障排查', '遇到技术问题时能自主排查根本原因，不依赖他人解决问题。'),
        ('COM', 0): ('解码能力', '准确理解对方言辞背后的真正意图，能处理含蓄和模糊的沟通。'),
        ('COM', 1): ('精炼表达', '用简洁清晰的语言表达复杂概念，书面和口头表达均逻辑清晰。'),
        ('COM', 2): ('口头影响力', '在公开发言和会议中能有效吸引听众注意力和影响决策。'),
        ('SOC', 0): ('情绪觉察', '敏锐感知他人情绪的细微变化，能根据对方状态调整沟通方式。'),
        ('SOC', 1): ('冲突协调', '在团队分歧和人际冲突中能促进各方达成共识，保持冷静。'),
        ('SOC', 2): ('关系建立', '与不同背景的人建立信任，维护长期人脉网络并保持有效联系。'),
        ('ORG', 0): ('目标规划', '将模糊目标拆解为清晰可衡量的行动步骤，制定详细计划和时间表。'),
        ('ORG', 1): ('自主执行', '在无外部监督的情况下仍能维持高标准，主动推进任务不拖延。'),
        ('ORG', 2): ('资源管理', '合理分配时间、人力、预算等资源，在有限条件下最大化产出。'),
        ('PRS', 0): ('应变能力', '原方案失败时能迅速调整策略，快速产出替代方案（Plan B）。'),
        ('PRS', 1): ('根源分析', '用结构化方法（5 Whys、鱼骨图等）深挖问题根本原因。'),
        ('PRS', 2): ('创新方案', '在无既有SOP的情况下能自行设计有效解决方案，常有创意突破。'),
        ('MGT', 0): ('预期管理', '有效管理上级和团队对任务结果的期望，避免目标与产出的落差。'),
        ('MGT', 1): ('优先级取舍', '多任务并行时能准确判断轻重缓急，敢于拒绝次要任务的干扰。'),
        ('MGT', 2): ('授权追踪', '有效分配任务并建立跟进机制，信任团队不过度干预执行过程。'),
        ('LLA', 0): ('知识更新', '保持定期阅读行业书刊、参加课程的习惯，主动更新专业知识体系。'),
        ('LLA', 1): ('主动探索', '跨界探索本职以外的新领域，好奇心驱动学习，不带功利目的。'),
        ('LLA', 2): ('挫折转化', '面对批评和失败能保持成长型心态，将负面反馈转化为改进养分。'),
    }
    
    # 维度中文名称
    dim_cn = {
        'COG': '认知能力', 'TEC': '技术掌握', 'COM': '理解表达',
        'SOC': '社交技能', 'ORG': '策划执行', 'PRS': '解决问题',
        'MGT': '管理技能', 'LLA': '持续学习'
    }

    # 维度图标
    dim_icon = {
        'COG': '🧠', 'TEC': '💻', 'COM': '💬',
        'SOC': '🤝', 'ORG': '🎯', 'PRS': '⚡',
        'MGT': '👥', 'LLA': '📚'
    }
    
    # 子能力颜色
    def sub_score_color(s):
        if s >= 3.5: return COLOR_ACCENT_GOOD  # 绿色
        elif s >= 2.5: return COLOR_ACCENT_WARN  # 橙色
        else: return colors.HexColor('#ef4444')  # 红色

    # ─── 需改进子能力（2个）──────────
    story.append(Paragraph("🔴 优先改进的子能力", ps('IMPH', fontName=font_name+'-Bold',
        fontSize=11, textColor=colors.HexColor('#dc2626'), spaceAfter=6)))
    
    for sub in need_improve:
        sub_key = (sub['dim'], sub['sub_idx'])
        sub_name, sub_desc = sub_cn_names.get(sub_key, (sub['name'], sub['desc']))
        dim_name = dim_cn.get(sub['dim'], sub['dim'])
        icon = dim_icon.get(sub['dim'], '📊')
        s_color = sub_score_color(sub['score'])
        
        card = Table([
            [Paragraph(f"<b>{icon} {sub_name}</b>",
                      ps('subn', fontName=font_name+'-Bold', fontSize=11,
                         textColor=s_color)),
             Paragraph(f"<b>{sub['score']:.1f}</b>/5.0",
                      ps('subs', fontName=font_name+'-Bold', fontSize=14,
                         textColor=s_color, alignment=TA_RIGHT))],
            [Paragraph(f"<b>所属维度：</b>{dim_name} | <b>等级：</b>{sub['level']}",
                      ps('subm', fontName=font_name, fontSize=8,
                         textColor=COLOR_TEXT_MID)),
             Paragraph("", ps('subsp', fontName=font_name, fontSize=8))],
            [Paragraph(sub_desc,
                      ps('subd', fontName=font_name, fontSize=9,
                         textColor=COLOR_TEXT_DARK, leading=13)), ""],
        ], colWidths=[370, 70])
        card.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#fff7ed')),
            ('TOPPADDING', (0,0), (-1,-1), 8), ('BOTTOMPADDING', (0,0), (-1,-1), 8),
            ('LEFTPADDING', (0,0), (-1,-1), 10), ('RIGHTPADDING', (0,0), (-1,-1), 10),
            ('LINEBELOW', (0,0), (-1,0), 1.5, colors.HexColor('#ea580c')),
            ('SPAN', (0,2), (1,2)),
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ]))
        story.append(card); story.append(Spacer(1, 6))

    story.append(Spacer(1, 4*mm))

    # ─── 需维持子能力（2个）──────────
    story.append(Paragraph("🟢 持续保持的子能力", ps('MNTH', fontName=font_name+'-Bold',
        fontSize=11, textColor=colors.HexColor('#059669'), spaceAfter=6)))
    
    for sub in need_maintain:
        sub_key = (sub['dim'], sub['sub_idx'])
        sub_name, sub_desc = sub_cn_names.get(sub_key, (sub['name'], sub['desc']))
        dim_name = dim_cn.get(sub['dim'], sub['dim'])
        icon = dim_icon.get(sub['dim'], '📊')
        s_color = sub_score_color(sub['score'])
        
        card = Table([
            [Paragraph(f"<b>{icon} {sub_name}</b>",
                      ps('subn2', fontName=font_name+'-Bold', fontSize=11,
                         textColor=s_color)),
             Paragraph(f"<b>{sub['score']:.1f}</b>/5.0",
                      ps('subs2', fontName=font_name+'-Bold', fontSize=14,
                         textColor=s_color, alignment=TA_RIGHT))],
            [Paragraph(f"<b>所属维度：</b>{dim_name} | <b>等级：</b>{sub['level']}",
                      ps('subm2', fontName=font_name, fontSize=8,
                         textColor=COLOR_TEXT_MID)),
             Paragraph("", ps('subsp2', fontName=font_name, fontSize=8))],
            [Paragraph(sub_desc + " 继续保持这项优势，在工作中充分发挥！",
                      ps('subd2', fontName=font_name, fontSize=9,
                         textColor=COLOR_TEXT_DARK, leading=13)), ""],
        ], colWidths=[370, 70])
        card.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#f0fdf4')),
            ('TOPPADDING', (0,0), (-1,-1), 8), ('BOTTOMPADDING', (0,0), (-1,-1), 8),
            ('LEFTPADDING', (0,0), (-1,-1), 10), ('RIGHTPADDING', (0,0), (-1,-1), 10),
            ('LINEBELOW', (0,0), (-1,0), 1.5, colors.HexColor('#10b981')),
            ('SPAN', (0,2), (1,2)),
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ]))
        story.append(card); story.append(Spacer(1, 6))

    story.append(Spacer(1, 4*mm))

    # 90天行动计划提示
    action_tip = (
        f"💡 <b>90天行动计划建议</b>："
        f"优先改进「{need_improve[0]['name']}」和「{need_improve[1]['name']}」这两项子能力，"
        f"每30天完成1次自检并记录进步幅度。"
    )
    action_box = Table([[Paragraph(action_tip, ps('actip', fontName=font_name, fontSize=9,
        textColor=COLOR_TEXT_DARK, leading=14))]], colWidths=[440])
    action_box.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#eff6ff')),
        ('TOPPADDING', (0,0), (-1,-1), 10), ('BOTTOMPADDING', (0,0), (-1,-1), 10),
        ('LEFTPADDING', (0,0), (-1,-1), 12), ('RIGHTPADDING', (0,0), (-1,-1), 12),
    ]))
    story.append(action_box)

    # ─── 页脚 ───
    story.append(Spacer(1, 8*mm))
    ft = Table([["© 2026 Santa Chow 香港求职咨询  |  8维能力测评报告"]], colWidths=[440])
    ft.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), COLOR_PRIMARY),
        ('TEXTCOLOR', (0,0), (-1,-1), colors.white),
        ('FONTNAME', (0,0), (-1,-1), font_name),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('TOPPADDING', (0,0), (-1,-1), 7), ('BOTTOMPADDING', (0,0), (-1,-1), 7)]))
    story.append(ft)

    doc.build(story)
    buffer.seek(0)
    return buffer

# ============ v3.3 四阶递进报告生成器 ============
# 基于 generate_mock_4stage_v3.py 集成，使用真实测评数据

from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Flowable
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm

# ─── v3.3 柔和配色方案 ───
V33_COLOR_PRIMARY = colors.HexColor('#8b7355')
V33_COLOR_PRIMARY_LIGHT = colors.HexColor('#f5f0eb')
V33_COLOR_CHARGE = colors.HexColor('#7d9b76')       # 柔和橄榄绿
V33_COLOR_CHARGE_LIGHT = colors.HexColor('#f5f8f3')
V33_COLOR_SHIELD = colors.HexColor('#c4907a')       # 柔和陶土橘
V33_COLOR_SHIELD_LIGHT = colors.HexColor('#faf5f3')
V33_COLOR_CREAM = colors.HexColor('#faf8f5')
V33_COLOR_BEIGE = colors.HexColor('#f5f0e8')
V33_COLOR_ACCENT = colors.HexColor('#e8d5c4')
V33_COLOR_WARM_GRAY = colors.HexColor('#9e948a')


class _GradientCard(Flowable):
    """带圆角和渐变背景的卡片"""
    def __init__(self, width, height, colors_list, corner_radius=8, direction='vertical'):
        Flowable.__init__(self)
        self.width = width
        self.height = height
        self.colors_list = colors_list
        self.corner_radius = corner_radius
        self.direction = direction

    def draw(self):
        c = self.canv
        c.saveState()
        if self.direction == 'vertical':
            p = c.beginPath()
            p.moveTo(self.corner_radius, 0)
            p.lineTo(self.width - self.corner_radius, 0)
            p.arcTo(self.width - 2*self.corner_radius, 0, self.width, 2*self.corner_radius, -90, 90)
            p.lineTo(self.width, self.height - self.corner_radius)
            p.arcTo(self.width - 2*self.corner_radius, self.height - 2*self.corner_radius, self.width, self.height, 0, 90)
            p.lineTo(self.corner_radius, self.height)
            p.arcTo(0, self.height - 2*self.corner_radius, 2*self.corner_radius, self.height, 90, 90)
            p.lineTo(0, self.corner_radius)
            p.arcTo(0, 0, 2*self.corner_radius, 2*self.corner_radius, 180, 90)
            p.close()
            gradient = c.linearGradient(0, 0, 0, self.height, self.colors_list)
        else:
            p = c.beginPath()
            p.moveTo(self.corner_radius, 0)
            p.lineTo(self.width - self.corner_radius, 0)
            p.arcTo(self.width - 2*self.corner_radius, 0, self.width, 2*self.corner_radius, -90, 90)
            p.lineTo(self.width, self.height - self.corner_radius)
            p.arcTo(self.width - 2*self.corner_radius, self.height - 2*self.corner_radius, self.width, self.height, 0, 90)
            p.lineTo(self.corner_radius, self.height)
            p.arcTo(0, self.height - 2*self.corner_radius, 2*self.corner_radius, self.height, 90, 90)
            p.lineTo(0, self.corner_radius)
            p.arcTo(0, 0, 2*self.corner_radius, 2*self.corner_radius, 180, 90)
            p.close()
            gradient = c.linearGradient(0, 0, self.width, 0, self.colors_list)
        c.clipPath(p, stroke=0)
        c.setFillColor(gradient)
        c.rect(0, 0, self.width, self.height, fill=1, stroke=0)
        c.restoreState()


# ─── 题目ID→维度映射（8d_quiz_48.html 的48题固定顺序）────
# 公式：(qid - 1) // 6 → 维度索引，qid从1开始
_DIM_ORDER = ['COG', 'TEC', 'COM', 'SOC', 'ORG', 'PRS', 'MGT', 'LLA']

def _qid_to_dim(qid):
    """根据题目ID返回维度"""
    if isinstance(qid, str):
        qid = int(qid)
    return _DIM_ORDER[(qid - 1) // 6]


# ─── 子能力名称映射 ───
DIM_SUB_NAMES = {
    'COG': ['信息提炼', '逻辑推理', '快速学习'],
    'TEC': ['数字生产力', '技术适应力', '故障排查'],
    'COM': ['解码能力', '精炼表达', '口头影响力'],
    'SOC': ['情绪觉察', '冲突协调', '关系建立'],
    'ORG': ['目标规划', '高标准执行', '资源管理'],
    'PRS': ['Plan B产出', '根源分析', '创新方案'],
    'MGT': ['任务预期管理', '优先级取舍', '授权追踪'],
    'LLA': ['知识更新', '主动探索', '挫折转化'],
}

DIM_CN_NAMES = {
    'COG': '认知能力', 'TEC': '技术掌握', 'COM': '理解表达',
    'SOC': '社交技能', 'ORG': '策划执行', 'PRS': '解决问题',
    'MGT': '管理技能', 'LLA': '持续学习',
}

DIM_Q_COUNTS = {
    'COG': 6, 'TEC': 6, 'COM': 6, 'SOC': 6,
    'ORG': 6, 'PRS': 6, 'MGT': 6, 'LLA': 6,
}


def _compute_sub_scores(scores, answers, question_order):
    """
    计算每个子能力的分数。
    answers: {qid_str: score}  e.g. {'1': 4, '2': 5, ...}
    question_order: list of qids [1, 2, 3, ...] or [{'id': 1, ...}, ...]
    """
    sub_scores = {}
    for dim, subs in DIM_SUB_NAMES.items():
        dim_data = scores.get(dim, {})
        dim_avg = dim_data.get('average', 3.0)
        sub_scores[dim] = []

        # 找出属于该维度的题目ID（支持ID列表或对象列表）
        if question_order:
            qids = []
            for q in question_order:
                if isinstance(q, dict):
                    qid = q.get('id') or q.get('qid')
                else:
                    qid = int(q)
                if _qid_to_dim(qid) == dim:
                    qids.append(qid)
        else:
            # fallback：用固定范围
            dim_idx = _DIM_ORDER.index(dim)
            qids = list(range(dim_idx * 6 + 1, dim_idx * 6 + 7))

        n_subs = len(subs)
        q_per_sub = len(qids) // n_subs if n_subs > 0 else 1
        for i, sub_name in enumerate(subs):
            sub_qids = qids[i * q_per_sub: (i + 1) * q_per_sub] if qids else []
            if sub_qids:
                avg = sum(answers.get(str(qid), 3) for qid in sub_qids) / len(sub_qids)
            else:
                avg = dim_avg  # fallback
            sub_scores[dim].append({'name': sub_name, 'score': avg})
    return sub_scores


def _v33_ps(name, font_name, **kwargs):
    fn = kwargs.pop('fontName', font_name)
    return ParagraphStyle(name + '_v33', fontName=fn, **kwargs)


def _level_label(s):
    if s >= 4.5: return '优秀'
    elif s >= 4.0: return '高'
    elif s >= 3.0: return '中'
    return '低'


def _score_tag(s):
    if s >= 4.0: return '求进武器'
    elif s >= 3.0: return '待释放'
    return '防护区'


def _weather_text(top_dim, top_score, bot_dim, bot_score):
    """生成心智状态提醒文本"""
    weather_map = {
        'COG': ('逻辑大脑', '信息提炼能力'),
        'TEC': ('技术雷达', '技术适应力'),
        'COM': ('表达引擎', '沟通影响力'),
        'SOC': ('情绪天线', '情绪感知力'),
        'ORG': ('执行引擎', '目标推进力'),
        'PRS': ('问题解决仪', '根源分析力'),
        'MGT': ('预期管理塔', '优先级判断力'),
        'LLA': ('学习加速器', '知识更新力'),
    }
    w1, d1 = weather_map.get(top_dim, ('核心能力', top_dim))
    w2, d2 = weather_map.get(bot_dim, ('待提升', bot_dim))
    cn_top = DIM_CN_NAMES.get(top_dim, top_dim)
    cn_bot = DIM_CN_NAMES.get(bot_dim, bot_dim)

    return (
        f"您的{w1}（{cn_top} {top_score:.1f}）正在全力运转，"
        f"请给您的{w2}（{cn_bot} {bot_score:.1f}）留出特别的关注时间，"
        f"防止它拖累您的整体表现。"
    )


def _advance_text(top_dims, top_subs):
    """生成求进视角文本"""
    if not top_dims:
        return "您的各项能力分布均衡，建议持续关注行业动态，保持学习的节奏。"
    texts = []
    for (dim, score, sub_name, sub_score) in top_subs[:3]:
        cn = DIM_CN_NAMES.get(dim, dim)
        texts.append(
            f"<b>能力组合{len(texts)+1}：</b>{cn}×{sub_name}（{sub_score:.1f}）——"
            f"您的 {sub_name} 与整体 {cn} 感知力形成协同，能在团队中建立独特价值。"
        )
    return "<br/>".join(texts)


def _shield_text(bot_dims, bot_subs):
    """生成避坑视角文本"""
    if not bot_dims:
        return "您的各项能力都在安全区间，继续保持即可。"
    texts = []
    for (dim, score, sub_name, sub_score) in bot_subs[:2]:
        cn = DIM_CN_NAMES.get(dim, dim)
        risk_map = {
            'COG': '信息过载时容易失去重点',
            'TEC': '技术更新期可能产生焦虑',
            'COM': '在高压沟通场景中可能表达不清晰',
            'SOC': '过度关注他人情绪可能消耗自身能量',
            'ORG': '完美主义可能导致拖延',
            'PRS': '面对长期未能解决的问题时容易挫败',
            'MGT': '多任务并行时可能优先级错乱',
            'LLA': '学习受挫时可能产生自我怀疑',
        }
        risk = risk_map.get(dim, '需要注意该能力领域的风险信号')
        texts.append(
            f"<b>{cn}·{sub_name}（{sub_score:.1f}）：</b>{risk}。"
            f"当您感觉\"不对劲\"时，请启动逻辑分析（COG）来自我诊断。"
        )
    return "<br/>".join(texts)


def _emotion_reframe(bot_dim, bot_score):
    """生成情绪正名文本"""
    if not bot_dim:
        return "您的心理韧性处于良好水平，请继续保持自我觉察。"
    cn = DIM_CN_NAMES.get(bot_dim, bot_dim)
    reframe_map = {
        'COG': (f"您的信息处理需求较高，当信息量超过承载极限时产生的疲惫感，",
                "是身体在提醒您需要系统性整理，而非能力不足。"),
        'TEC': (f"您对技术更新有较高的敏感度，当面对\"学不完\"的焦虑时，",
                "请记住：您不需要掌握所有技术，只需要掌握最适合您的技术。"),
        'COM': (f"您对沟通效果有较高期待，当表达未能达到预期时产生的挫败感，",
                "是您在追求高质量沟通的信号。继续练习，您的表达力正在稳步提升。"),
        'SOC': (f"您对他人的情绪变化较为敏感，这种\"读空气\"的能力是您的优势，",
                "但请记得：感知到情绪后，选择如何回应是您的主动行为。"),
        'ORG': (f"您对目标和执行有较高的标准，当进展不如预期时产生的焦虑，",
                "是您高标准的表现，请区分\"不完美\"和\"失败\"的区别。"),
        'PRS': (f"您对问题解决有较高的期待，当方案未能完美实施时产生的挫败感，",
                "是您在追求卓越的信号。接受\"足够好\"也是进步的一部分。"),
        'MGT': (f"您对预期管理有较强的意识，当实际情况偏离预期时产生的紧张，",
                "是您的风险预警系统在起作用。请将情绪信息转化为调整策略的行动。"),
        'LLA': (f"您对成长有较高的期待，当学习进展缓慢时产生的焦虑，",
                "请记住：学习是长期积累的过程，每天的微小进步都值得肯定。"),
    }
    top, bot = reframe_map.get(bot_dim, (
        f"您的{cn}领域有提升空间，产生的焦虑或不安感，",
        "是您对自身有更高要求的体现，而非能力不足。"
    ))
    return f"{top}，{bot}"


def _scene_advance(top_dim, top_score, sub_name, sub_score):
    """生成求进型场景文本"""
    cn = DIM_CN_NAMES.get(top_dim, top_dim)
    scenarios = {
        'COG': {
            '信息提炼': (f"您的{cn}·{sub_name}（{sub_score:.1f}）是您的\"信息萃取器\"。"
                        "当团队陷入信息噪音时，您能用3分钟提炼出核心矛盾。"),
            '逻辑推理': (f"您的{cn}·{sub_name}（{sub_score:.1f}）是您的\"决策加速器\"。"
                        "当团队讨论偏离主题时，您能用逻辑框架快速收敛。"),
            '快速学习': (f"您的{cn}·{sub_name}（{sub_score:.1f}）是您的\"竞争力护城河\"。"
                        "当新技术出现时，您能在2-3周内从陌生到可用。"),
        },
        'TEC': {
            '数字生产力': (f"您的{cn}·{sub_name}（{sub_score:.1f}）是您的\"效率倍增器\"。"
                          "当重复性工作消耗您的精力时，用自动化工具释放时间。"),
            '技术适应力': (f"您的{cn}·{sub_name}（{sub_score:.1f}）是您的\"技术嗅觉\"。"
                          "当行业技术趋势发生变化时，您比他人更快嗅到机会。"),
            '故障排查': (f"您的{cn}·{sub_name}（{sub_score:.1f}）是您的\"系统诊断力\"。"
                       "当项目出现问题时，您能快速定位根本原因。"),
        },
        'SOC': {
            '情绪觉察': (f"您的{cn}·{sub_name}（{sub_score:.1f}）是您的\"人际雷达\"。"
                        "当团队氛围微妙变化时，您比他人更早察觉并调整策略。"),
            '冲突协调': (f"您的{cn}·{sub_name}（{sub_score:.1f}）是您的\"关系润滑剂\"。"
                        "当团队出现摩擦时，您能化解紧张并重建协作。"),
            '关系建立': (f"您的{cn}·{sub_name}（{sub_score:.1f}）是您的\"人脉资产\"。"
                        "当您需要跨部门协作时，您的关系网络是您的信用背书。"),
        },
        'ORG': {
            '目标规划': (f"您的{cn}·{sub_name}（{sub_score:.1f}）是您的\"方向导航仪\"。"
                        "当方向不清晰时，您能设定清晰的里程碑来推进团队。"),
            '高标准执行': (f"您的{cn}·{sub_name}（{sub_score:.1f}）是您的\"质量守护神\"。"
                         "当质量开始滑坡时，您能在无人督促下自我修复。"),
            '资源管理': (f"您的{cn}·{sub_name}（{sub_score:.1f}）是您的\"资源优化器\"。"
                       "当资源有限时，您能找到最优配置方案。"),
        },
        'PRS': {
            'Plan B产出': (f"您的{cn}·{sub_name}（{sub_score:.1f}）是您的\"风险缓冲器\"。"
                          "当Plan A受阻时，您能快速产出替代方案。"),
            '根源分析': (f"您的{cn}·{sub_name}（{sub_score:.1f}）是您的\"问题显微镜\"。"
                       "当表面问题反复出现时，您能追根溯源彻底解决。"),
            '创新方案': (f"您的{cn}·{sub_name}（{sub_score:.1f}）是您的\"创意火花塞\"。"
                       "当常规方案失效时，您能跳出框架提出新思路。"),
        },
        'MGT': {
            '任务预期管理': (f"您的{cn}·{sub_name}（{sub_score:.1f}）是您的\"期望校准器\"。"
                           "当上下级预期不一致时，您能主动对齐并管理期望。"),
            '优先级取舍': (f"您的{cn}·{sub_name}（{sub_score:.1f}）是您的\"决策过滤器\"。"
                          "当资源有限时，您能果断取舍并承担决策责任。"),
            '授权追踪': (f"您的{cn}·{sub_name}（{sub_score:.1f}）是您的\"信任桥梁\"。"
                       "当授权他人后，您能适度追踪而不 micromanage。"),
        },
        'LLA': {
            '知识更新': (f"您的{cn}·{sub_name}（{sub_score:.1f}）是您的\"知识更新引擎\"。"
                        "当行业知识快速迭代时，您能持续保持认知竞争力。"),
            '主动探索': (f"您的{cn}·{sub_name}（{sub_score:.1f}）是您的\"好奇心引擎\"。"
                        "当机会出现时，您比他人更早发现并行动。"),
            '挫折转化': (f"您的{cn}·{sub_name}（{sub_score:.1f}）是您的\"心理修复力\"。"
                        "当遭遇挫折时，您能快速调整并从中学到经验。"),
        },
    }
    default = f"您的{cn}·{sub_name}（{sub_score:.1f}）是您的竞争优势，在职场中善用这项能力。"
    return scenarios.get(top_dim, {}).get(sub_name, default)


def _scene_shield(bot_dim, bot_score, top_dim=None, top_score=None):
    """生成避坑型场景文本"""
    cn = DIM_CN_NAMES.get(bot_dim, bot_dim)
    scenario_intro = f"情境：识别并防御{cn}领域的潜在陷阱"

    risk_map = {
        'COG': ("您在复杂信息中容易\"眉毛胡子一把抓\"，",
                "建议：每次会议后用3句话总结\"决策点、风险点、行动项\"。"),
        'TEC': ("当多个技术栈同时更新时，您可能产生\"技术焦虑\"，",
                "建议：设定技术学习的\"最低可行知识线\"，不追求完美掌握。"),
        'COM': ("在高压对话中，您可能过度斟酌措辞而错过表达时机，",
                "建议：预设3个\"万能开场白\"，让开口变得更容易。"),
        'SOC': ("您对他人的情绪过于敏感，可能吸收他人的负面情绪，",
                "建议：每次感知到他人情绪时，问自己\"这是他的还是我的？\"。"),
        'ORG': ("您的完美主义可能导致\"启动拖延\"，迟迟不能开始行动，",
                "建议：设定\"最小行动\"标准，先完成再完美。"),
        'PRS': ("当一个问题长期无法解决时，您可能陷入\"解决执念\"，",
                "建议：设定\"止损线\"——如果3次尝试失败，就换一个思路。"),
        'MGT': ("多任务并行时，您可能难以取舍，导致精力分散，",
                "建议：每天只确定1个\"核心任务\"，其他都是\"加分项\"。"),
        'LLA': ("当学习效果不如预期时，您可能产生\"进步焦虑\"，",
                "建议：记录\"微进步日志\"，用具体数据对抗模糊的焦虑感。"),
    }
    top, bot = risk_map.get(bot_dim, (
        f"您的{cn}领域需要特别关注，",
        "建议：建立该领域的自查清单，提前识别风险信号。"
    ))

    behavior = (f"<b>觉察时刻：</b>当您感到{cn}相关的压力时，请立即启动预警模式。<br/>"
                f"<b>风险信号：</b>{top}<br/>"
                f"<b>防御策略：</b>{bot}")
    return scenario_intro, behavior


def _strategy_advance(top_dims, top_subs):
    """生成优势扩容策略"""
    if not top_dims:
        return "继续关注行业动态，保持学习的节奏。"
    strategies = []
    for i, (dim, score, sub_name, sub_score) in enumerate(top_subs[:3], 1):
        cn = DIM_CN_NAMES.get(dim, dim)
        strategy_map = {
            'COG': {
                '信息提炼': (f"<b>策略{i}：从「信息接收者」升级为「信息架构师」</b><br/>"
                           f"您的{sub_name}（{sub_score:.1f}）能让您快速理解复杂信息。<br/>"
                           "<b>做法：</b>每周整理一份\"3页行业洞察\"，发给团队或发布到内部平台。<br/>"
                           "<b>收益：</b>您的影响力从\"个人理解力强\"升级为\"能帮团队对齐认知\"。"),
                '逻辑推理': (f"<b>策略{i}：从「分析员」升级为「决策伙伴」</b><br/>"
                           f"您的{sub_name}（{sub_score:.1f}）是您的逻辑引擎。<br/>"
                           "<b>做法：</b>每次汇报时增加\"建议选项 + 每个选项的利弊\"。<br/>"
                           "<b>收益：</b>您的价值从\"能分析问题\"升级为\"能帮上级做决定\"。"),
                '快速学习': (f"<b>策略{i}：从「学习者」升级为「知识传递者」</b><br/>"
                           f"您的{sub_name}（{sub_score:.1f}）是您的学习加速器。<br/>"
                           "<b>做法：</b>每学到一个新知识点，用\"15分钟精华分享\"内化。<br/>"
                           "<b>收益：</b>您的学习效率通过\"教中学\"进一步提升，同时建立团队影响力。"),
            },
            'TEC': {
                '数字生产力': (f"<b>策略{i}：建立您的「效率工具箱」</b><br/>"
                             f"您的{sub_name}（{sub_score:.1f}）让您擅长使用数字工具。<br/>"
                             "<b>做法：</b>找到3个能让您每天节省30分钟的必备工具，并分享给团队。<br/>"
                             "<b>收益：</b>您成为团队的\"效率顾问\"，不可替代性增加。"),
                '技术适应力': (f"<b>策略{i}：从「技术使用者」升级为「技术布道者」</b><br/>"
                             f"您的{sub_name}（{sub_score:.1f}）让您在技术更新中保持领先。<br/>"
                             "<b>做法：</b>每当掌握一项新技术，主动承担\"内部培训师\"角色。<br/>"
                             "<b>收益：</b>您的技术影响力从\"个人强\"升级为\"能带动团队成长\"。"),
                '故障排查': (f"<b>策略{i}：成为团队的「系统守护者」</b><br/>"
                           f"您的{sub_name}（{sub_score:.1f}）让您能快速定位和解决问题。<br/>"
                           "<b>做法：</b>建立故障复盘文档库，形成团队知识沉淀。<br/>"
                           "<b>收益：</b>您的价值从\"能修bug\"升级为\"能防止bug发生\"。"),
            },
            'SOC': {
                '情绪觉察': (f"<b>策略{i}：用「情绪雷达」做团队关系的提前干预</b><br/>"
                           f"您的{sub_name}（{sub_score:.1f}）让您在冲突爆发前就能感知信号。<br/>"
                           "<b>做法：</b>感知到团队氛围变化时，用私人沟通而非公开场合干预。<br/>"
                           "<b>收益：</b>您成为团队关系的\"维护者\"，在组织中拥有独特的软实力。"),
                '冲突协调': (f"<b>策略{i}：成为「会议终结者」</b><br/>"
                           f"您的{sub_name}（{sub_score:.1f}）让您在冲突中保持建设性。<br/>"
                           "<b>做法：</b>当会议陷入僵局时，提出\"共同目标\"框架来重新对齐。<br/>"
                           "<b>收益：</b>您成为组织中解决复杂人际问题的首选人物。"),
                '关系建立': (f"<b>策略{i}：建立您的「关键关系地图」</b><br/>"
                           f"您的{sub_name}（{sub_score:.1f}）让您在组织中建立有价值的连接。<br/>"
                           "<b>做法：</b>每月与一位跨部门同事进行15分钟coffee chat。<br/>"
                           "<b>收益：</b>您的协作效率和信息获取能力显著提升。"),
            },
            'ORG': {
                '目标规划': (f"<b>策略{i}：成为「里程碑设定专家」</b><br/>"
                           f"您的{sub_name}（{sub_score:.1f}）让您擅长设定清晰的目标路径。<br/>"
                           "<b>做法：</b>每个项目开始时，先输出\"OKR草案\"与团队对齐。<br/>"
                           "<b>收益：</b>您成为团队方向感的\"锚点\"，战略价值凸显。"),
                '高标准执行': (f"<b>策略{i}：将「执行标准」显性化</b><br/>"
                            f"您的{sub_name}（{sub_score:.1f}）让您对质量有本能的坚持。<br/>"
                            "<b>做法：</b>将您的质量标准写成checklist，让团队成员也能对齐。<br/>"
                            "<b>收益：</b>您的标准从\"个人习惯\"升级为\"团队规范\"，扩大影响力。"),
                '资源管理': (f"<b>策略{i}：成为「资源谈判高手」</b><br/>"
                           f"您的{sub_name}（{sub_score:.1f}）让您擅长优化资源配置。<br/>"
                           "<b>做法：</b>在资源申请时，用\"投入产出比\"框架做论据。<br/>"
                           "<b>收益：</b>您成为组织中资源分配的\"智囊\"，被邀请参与战略决策。"),
            },
            'PRS': {
                'Plan B产出': (f"<b>策略{i}：将「备用方案」纳入项目管理标准流程</b><br/>"
                             f"您的{sub_name}（{sub_score:.1f}）让您在危机中保持冷静。<br/>"
                             "<b>做法：</b>每个重要项目立项时，同时输出Plan A和Plan B。<br/>"
                             "<b>收益：</b>您成为组织中应对不确定性的\"定海神针\"。"),
                '根源分析': (f"<b>策略{i}：成为「5-Why大师」</b><br/>"
                           f"您的{sub_name}（{sub_score:.1f}）让您在问题初期就能找到根本原因。<br/>"
                           "<b>做法：</b>遇到问题时，追问5层\"为什么\"，并将分析过程文档化。<br/>"
                           "<b>收益：</b>您成为团队的问题诊断专家，减少重复救火的消耗。"),
                '创新方案': (f"<b>策略{i}：建立「创新实验日志」</b><br/>"
                           f"您的{sub_name}（{sub_score:.1f}）让您有突破常规的创造力。<br/>"
                           "<b>做法：</b>每周尝试一个\"小实验\"——哪怕是改进一个工作流程的细节。<br/>"
                           "<b>收益：</b>您成为组织中的\"创新引擎\"，在变革期拥有不可替代的价值。"),
            },
            'MGT': {
                '任务预期管理': (f"<b>策略{i}：成为「预期校准器」</b><br/>"
                               f"您的{sub_name}（{sub_score:.1f}）让您擅长管理上下级预期。<br/>"
                               "<b>做法：</b>每个任务开始前，用\"我理解的目标是...\"做确认。<br/>"
                               "<b>收益：</b>您减少因预期错位导致的返工，成为上级信任的合作者。"),
                '优先级取舍': (f"<b>策略{i}：建立您的「优先级决策框架」</b><br/>"
                              f"您的{sub_name}（{sub_score:.1f}）让您在取舍时有清晰的逻辑。<br/>"
                              "<b>做法：</b>使用\"影响力×紧迫度\"矩阵做每日决策，并记录决策依据。<br/>"
                              "<b>收益：</b>您成为团队的资源配置专家，能高效推动最重要的事。"),
                '授权追踪': (f"<b>策略{i}：从「亲力亲为」升级为「赋能型管理者」</b><br/>"
                           f"您的{sub_name}（{sub_score:.1f}）让您在授权后保持有效追踪。<br/>"
                           "<b>做法：</b>授权时明确\"里程碑节点\"，而非\"每日检查\"。<br/>"
                           "<b>收益：</b>您能承接更多责任，同时团队成员有成长空间。"),
            },
            'LLA': {
                '知识更新': (f"<b>策略{i}：建立「知识资产」而非「收藏夹」</b><br/>"
                           f"您的{sub_name}（{sub_score:.1f}）让您持续更新认知。<br/>"
                           "<b>做法：</b>每学到一个知识点，写\"如何应用这条知识的一句话行动\"。<br/>"
                           "<b>收益：</b>您的知识从\"输入\"转化为\"产出\"，积累可变现的专业壁垒。"),
                '主动探索': (f"<b>策略{i}：成为「机会雷达」</b><br/>"
                           f"您的{sub_name}（{sub_score:.1f}）让您在早期发现趋势和机会。<br/>"
                           "<b>做法：</b>每周花30分钟浏览行业外的前沿信息，寻找跨界灵感。<br/>"
                           "<b>收益：</b>您的视野超越本岗位，成为组织的\"战略传感器\"。"),
                '挫折转化': (f"<b>策略{i}：将「挫折」变成「实验数据」</b><br/>"
                           f"您的{sub_name}（{sub_score:.1f}）让您能在挫折后快速恢复。<br/>"
                           "<b>做法：</b>每次挫折后，用\"实验视角\"总结：假设是什么？数据是什么？下次怎么改？<br/>"
                           "<b>收益：</b>您将挫折从\"情绪负担\"转化为\"成长养分\"，心理韧性持续增强。"),
            },
        }
        dim_strategies = strategy_map.get(dim, {})
        default_strategy = (f"<b>策略{i}：深化{cn}·{sub_name}</b><br/>"
                          f"您的{sub_name}（{sub_score:.1f}）是您的竞争优势。<br/>"
                          "<b>做法：</b>找到该能力可以产生最大影响的1-2个场景，持续深耕。<br/>"
                          "<b>收益：</b>您在该领域建立不可替代的专业壁垒。")
        strategies.append(dim_strategies.get(sub_name, default_strategy))
    return "<br/><br/>".join(strategies)


def _safety_text(bot_dims, bot_subs):
    """生成安全垫策略"""
    if not bot_dims:
        return "您的各项能力都在安全区间，建议继续保持均衡发展。"
    safety_map = {
        'COG': ("不要用意志力对抗信息过载，用「信息分层过滤法」来管理："
                "每天早上确定\"今日必须关注的3件事\"，其他信息设为\"稍后处理\"。"),
        'TEC': ("不要试图掌握所有新技术，用「技能树规划法」来管理："
                "只学习与当前岗位直接相关的技术，其他保持\"听说过\"即可。"),
        'COM': ("不要追求完美的口头表达，用「结构化表达模板」来管理："
                "准备3个万能开场白和1个总结模板，让开口变得更机械而非依赖状态。"),
        'SOC': ("不要过度吸收他人情绪，用「情绪边界」来保护自己："
                "感知到他人情绪后，问自己\"这是他的情绪，我选择怎么回应\"。"),
        'ORG': ("不要用意志力对抗完美主义，用「最小可行版本」来启动："
                "每次先产出\"60分版本\"，再决定是否继续优化。"),
        'PRS': ("不要陷入「解决执念」，用「止损线」来管理："
                "如果一个方案3次尝试后仍未解决，立即切换到Plan B或寻求帮助。"),
        'MGT': ("不要同时处理太多任务，用「单点专注法」来管理："
                "每天确定1个核心任务，其他任务设为\"加分项\"而非\"必须项\"。"),
        'LLA': ("不要追求立竿见影的进步，用「微积累日志」来管理："
                "每天记录1条\"今日学到的\"，用具体数据对抗\"什么都没学到\"的焦虑感。"),
    }
    safety_map_full = {
        'COG': ("不要用意志力对抗信息过载，用「信息分层过滤法」来管理："
                "每天早上确定\"今日必须关注的3件事\"，其他信息设为\"稍后处理\"。<br/>"
                "<b>避坑话术：</b>\"关于这个问题，我需要先整理一下核心信息，明天给您一个结构化的分析报告。\""),
        'TEC': ("不要试图掌握所有新技术，用「技能树规划法」来管理："
                "只学习与当前岗位直接相关的技术，其他保持\"听说过\"即可。<br/>"
                "<b>避坑话术：</b>\"关于这个新技术，我的建议是先做一个POC验证核心价值，同时我来做一份技术对比分析。\""),
        'COM': ("不要追求完美的口头表达，用「结构化表达模板」来管理："
                "准备3个万能开场白和1个总结模板，让开口变得更机械而非依赖状态。<br/>"
                "<b>避坑话术：</b>\"关于这个话题，我的核心观点是……让我用3个要点来展开。\""),
        'SOC': ("不要过度吸收他人情绪，用「情绪边界」来保护自己："
                "感知到他人情绪后，问自己\"这是他的情绪，我选择怎么回应\"。<br/>"
                "<b>避坑话术：</b>\"我注意到你今天情绪不太好，需要我帮你分担一些工作吗？\""),
        'ORG': ("不要用意志力对抗完美主义，用「最小可行版本」来启动："
                "每次先产出\"60分版本\"，再决定是否继续优化。<br/>"
                "<b>避坑话术：</b>\"这个方案目前是60分，核心功能已经可用，我们先推进，遇到问题再迭代。\""),
        'PRS': ("不要陷入「解决执念」，用「止损线」来管理："
                "如果一个方案3次尝试后仍未解决，立即切换到Plan B或寻求帮助。<br/>"
                "<b>避坑话术：</b>\"我们尝试了3个方向，我认为应该启动备用方案，争取在deadline前交付。\""),
        'MGT': ("不要同时处理太多任务，用「单点专注法」来管理："
                "每天确定1个核心任务，其他任务设为\"加分项\"而非\"必须项\"。<br/>"
                "<b>避坑话术：</b>\"关于A和B两个任务，如果要保证质量，我需要先和你对齐优先级，你看我们约个5分钟？\""),
        'LLA': ("不要追求立竿见影的进步，用「微积累日志」来管理："
                "每天记录1条\"今日学到的\"，用具体数据对抗\"什么都没学到\"的焦虑感。<br/>"
                "<b>代偿方案：</b>当学习受挫时，启动\"3问法\"：① 这个挫折的根本原因是什么？② 我能控制什么？③ 下次只需改变哪个环节？"),
    }

    sections = []
    for (dim, score, sub_name, sub_score) in bot_subs[:2]:
        cn = DIM_CN_NAMES.get(dim, dim)
        strategy = safety_map_full.get(dim,
            f"不要用意志力硬扛，用您的优势能力去绕过它。<br/>"
            f"<b>代偿方案：</b>当{dim}领域出现压力时，立即启动您最强的能力来提供结构性支持。")
        sections.append(
            f"<b>安全垫：{cn}·{sub_name}（{sub_score:.1f}）</b><br/>"
            f"{strategy}"
        )
    return "<br/><br/>".join(sections)


def generate_pdf_48_v33(result_id, scores, answers, user_name, experience, question_order=None, font_name='Helvetica'):
    """
    生成 v3.3 四阶递进式 PDF 报告（柔和圆角版）
    签名兼容 generate_pdf_48_v4，新增 question_order 参数用于计算子能力。
    """
    if not font_name:
        font_name = 'Helvetica'

    if question_order is None:
        question_order = []

    import io as _io
    buffer = _io.BytesIO()

    # 计算子能力分数
    sub_scores = _compute_sub_scores(scores, answers, question_order)

    # 收集所有分数
    all_dim_scores = [(dim, scores.get(dim, {}).get('average', 3.0)) for dim in scores]
    sorted_dims = sorted(all_dim_scores, key=lambda x: x[1], reverse=True)
    top_dims = sorted_dims[:3]    # Top 3 优势
    bot_dims = sorted_dims[-3:]   # Bottom 3 待提升

    # 收集所有子能力分数
    all_subs = []
    for dim, subs in sub_scores.items():
        for sub in subs:
            all_subs.append((dim, sub['score'], sub['name'], sub['score']))
    sorted_subs = sorted(all_subs, key=lambda x: x[1], reverse=True)
    top_subs = sorted_subs[:3]
    bot_subs = sorted_subs[-3:]

    highest = sorted_dims[0] if sorted_dims else ('COG', 3.0)
    lowest = sorted_dims[-1] if sorted_dims else ('LLA', 3.0)
    top_dim, top_score = highest
    bot_dim, bot_score = lowest

    # ─── 构建文档 ───
    PAGE_W = 440

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm,
        topMargin=18*mm, bottomMargin=18*mm
    )
    story = []

    def ps(name, **kwargs):
        return _v33_ps(name, font_name, **kwargs)

    # ═════════════════════════════════════════════
    # 第一阶段：【画像】职场禀赋与双向定位
    # ═════════════════════════════════════════════
    story.append(Paragraph("第一阶段：【画像】职场禀赋与双向定位",
        ps('S1TITLE', fontSize=14, textColor=V33_COLOR_PRIMARY, spaceAfter=5)))
    story.append(Paragraph("Phase 1: Professional Profile & Dual Positioning",
        ps('S1SUB', fontSize=8, textColor=V33_COLOR_WARM_GRAY, spaceAfter=10)))

    weather_text = _weather_text(top_dim, top_score, bot_dim, bot_score)
    weather_card = Table([[Paragraph(
        f"<b>🌤️ 今日心智状态提醒</b><br/>{weather_text}",
        ps('WEATHER', fontSize=9, textColor=colors.HexColor('#5d4e37'), leading=14)
    )]], colWidths=[PAGE_W])
    weather_card.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#fdf8f3')),
        ('TOPPADDING', (0,0), (-1,-1), 12), ('BOTTOMPADDING', (0,0), (-1,-1), 12),
        ('LEFTPADDING', (0,0), (-1,-1), 14), ('RIGHTPADDING', (0,0), (-1,-1), 14),
        ('BOX', (0,0), (-1,-1), 1.5, V33_COLOR_ACCENT),
        ('ROUNDEDCORNERS', [8]),
    ]))
    story.append(weather_card)
    story.append(Spacer(1, 5*mm))

    # 数据锚点表
    anchor_data = [['数据锚点（≥4.0 高分项）', '', '', '']]
    for i, (dim, score, sub_name, sub_s) in enumerate(top_subs, 1):
        anchor_data.append([f'高分{i}', f'{DIM_CN_NAMES.get(dim,dim)} - {sub_name}',
                           f'{sub_s:.1f}', _level_label(sub_s)])
    anchor_table = Table(anchor_data, colWidths=[80, 190, 60, 60])
    anchor_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), V33_COLOR_CHARGE),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), font_name+'-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 9),
        ('FONTNAME', (0,1), (-1,-1), font_name),
        ('FONTSIZE', (0,1), (-1,-1), 9),
        ('BACKGROUND', (0,1), (-1,-1), V33_COLOR_CHARGE_LIGHT),
        ('TEXTCOLOR', (0,1), (0,-1), V33_COLOR_CHARGE),
        ('TEXTCOLOR', (3,1), (3,-1), colors.HexColor('#5a7a52')),
        ('ALIGN', (0,0), (0,-1), 'CENTER'),
        ('ALIGN', (2,0), (3,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 6), ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('ROUNDEDCORNERS', [6, 6, 0, 0]),
        ('LINEBELOW', (0,1), (-1,-2), 0.5, colors.HexColor('#d4e2ce')),
    ]))
    story.append(anchor_table)
    story.append(Spacer(1, 4*mm))

    # 求进视角
    adv_title = Paragraph(
        f"<b>求进视角（职业晋升的加速器）</b>",
        ps('ADV_TITLE', fontSize=11, textColor=colors.HexColor('#5a7a52'), spaceAfter=5))
    story.append(adv_title)
    adv_desc = _advance_text(top_dims, top_subs)
    adv_card = Table([[Paragraph(adv_desc, ps('ADV_DESC',
        fontSize=9, textColor=colors.HexColor('#3d4a35'), leading=13))]],
        colWidths=[PAGE_W])
    adv_card.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#f5f9f4')),
        ('TOPPADDING', (0,0), (-1,-1), 10), ('BOTTOMPADDING', (0,0), (-1,-1), 10),
        ('LEFTPADDING', (0,0), (-1,-1), 14), ('RIGHTPADDING', (0,0), (-1,-1), 14),
        ('LINEABOVE', (0,0), (-1,0), 3, V33_COLOR_CHARGE),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#c8dcc7')),
        ('ROUNDEDCORNERS', [8]),
    ]))
    story.append(adv_card)
    story.append(Spacer(1, 4*mm))

    # 避坑视角
    sh_title = Paragraph(
        f"<b>避坑视角（过滤职场噪音）</b>",
        ps('SHIELD_TITLE', fontSize=11, textColor=colors.HexColor('#9e6555'), spaceAfter=5))
    story.append(sh_title)
    sh_desc = _shield_text(bot_dims, bot_subs)
    sh_card = Table([[Paragraph(sh_desc, ps('SHIELD_DESC',
        fontSize=9, textColor=colors.HexColor('#4a3d35'), leading=13))]],
        colWidths=[PAGE_W])
    sh_card.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#faf6f4')),
        ('TOPPADDING', (0,0), (-1,-1), 10), ('BOTTOMPADDING', (0,0), (-1,-1), 10),
        ('LEFTPADDING', (0,0), (-1,-1), 14), ('RIGHTPADDING', (0,0), (-1,-1), 14),
        ('LINEABOVE', (0,0), (-1,0), 3, V33_COLOR_SHIELD),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#e0c4ba')),
        ('ROUNDEDCORNERS', [8]),
    ]))
    story.append(sh_card)
    story.append(Spacer(1, 5*mm))
    story.append(Paragraph(
        f"高分能力是您的矛，但了解它背后的代价，才是真正让您<b>「在攻守之间找到平衡」</b>的关键。",
        ps('HOOK1', fontSize=9, textColor=V33_COLOR_WARM_GRAY, leading=13, spaceAfter=10)))

    # ═════════════════════════════════════════════
    # 第二阶段：【动态张力】成就背后的心智成本
    # ═════════════════════════════════════════════
    story.append(PageBreak())
    story.append(Paragraph("第二阶段：【动态张力】成就背后的心智成本",
        ps('S2TITLE', fontSize=14, textColor=V33_COLOR_PRIMARY, spaceAfter=5)))
    story.append(Paragraph("Phase 2: Psychological Cost of Achievement",
        ps('S2SUB', fontSize=8, textColor=V33_COLOR_WARM_GRAY, spaceAfter=10)))

    anchor2_data = [['心理张力数据锚点', '', '', '']]
    for label, (dim, score) in [('求进动力', top_dims[0] if top_dims else ('COG', 3.0)),
                                 ('需关注', bot_dims[0] if bot_dims else ('LLA', 3.0))]:
        anchor2_data.append([label, f'{DIM_CN_NAMES.get(dim,dim)}', f'{score:.1f}', _level_label(score)])
    anchor2_table = Table(anchor2_data, colWidths=[80, 190, 70, 60])
    anchor2_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#9b8a7a')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), font_name+'-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 9),
        ('FONTNAME', (0,1), (-1,-1), font_name),
        ('FONTSIZE', (0,1), (-1,-1), 9),
        ('BACKGROUND', (0,1), (-1,1), V33_COLOR_CHARGE_LIGHT),
        ('BACKGROUND', (0,2), (-1,2), colors.HexColor('#fdf6f0')),
        ('TEXTCOLOR', (0,1), (0,1), V33_COLOR_CHARGE),
        ('TEXTCOLOR', (0,2), (0,2), V33_COLOR_SHIELD),
        ('TEXTCOLOR', (3,2), (3,2), colors.HexColor('#9e6555')),
        ('ALIGN', (0,0), (0,-1), 'CENTER'),
        ('ALIGN', (2,0), (3,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 6), ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('ROUNDEDCORNERS', [6, 6, 0, 0]),
        ('LINEBELOW', (0,1), (-1,1), 0.5, colors.HexColor('#d4e2ce')),
    ]))
    story.append(anchor2_table)
    story.append(Spacer(1, 4*mm))

    # 情绪正名
    emotion_title = Paragraph("<b>情绪正名：这不是弱点</b>",
        ps('EMOTION_TITLE', fontSize=11, textColor=colors.HexColor('#5a7a52'), spaceAfter=5))
    story.append(emotion_title)
    emotion_desc = _emotion_reframe(bot_dim, bot_score)
    emotion_card = Table([[Paragraph(
        f"<b>请务必理解：</b>{emotion_desc}",
        ps('EMOTION_DESC', fontSize=9, textColor=colors.HexColor('#3d4a35'), leading=13)
    )]], colWidths=[PAGE_W])
    emotion_card.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#f5f9f4')),
        ('TOPPADDING', (0,0), (-1,-1), 10), ('BOTTOMPADDING', (0,0), (-1,-1), 10),
        ('LEFTPADDING', (0,0), (-1,-1), 14), ('RIGHTPADDING', (0,0), (-1,-1), 14),
        ('LINEABOVE', (0,0), (-1,0), 3, V33_COLOR_CHARGE),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#c8dcc7')),
        ('ROUNDEDCORNERS', [8]),
    ]))
    story.append(emotion_card)
    story.append(Spacer(1, 4*mm))

    # 避坑预警
    warning_title = Paragraph("<b>避坑预警：求进路上的隐形陷阱</b>",
        ps('WARNING_TITLE', fontSize=11, textColor=colors.HexColor('#9e6555'), spaceAfter=5))
    story.append(warning_title)
    warning_risks = {
        'COG': ("当信息量超过承载极限时，您可能感到\"大脑过载\"而非\"不够聪明\"，"
                "请记住：整理信息不是额外工作，而是高效工作的基础设施。"),
        'TEC': ("当多个技术栈同时更新时，您可能产生\"学不完\"的焦虑，"
                "请记住：不需要掌握所有技术，只需要掌握对您当前岗位最关键的2-3个。"),
        'COM': ("在高压对话中，您可能过度斟酌而错过表达时机，"
                "请记住：沟通质量取决于准备，而非临场发挥。提前准备3个万能开场白。"),
        'SOC': ("您对他人的情绪过于敏感，可能无意识地吸收他人的负面情绪，"
                "请记住：感知情绪是能力，选择如何回应才是您的主动行为。"),
        'ORG': ("您的完美主义可能导致\"启动拖延\"，迟迟不能开始行动，"
                "请记住：先产出\"60分版本\"，再决定是否继续优化。启动比完美更重要。"),
        'PRS': ("当一个问题长期无法解决时，您可能陷入\"解决执念\"，"
                "请记住：设定\"止损线\"——如果3次尝试失败，就换一个思路。"),
        'MGT': ("多任务并行时，您可能难以取舍，导致精力分散，"
                "请记住：每天只确定1个\"核心任务\"，其他都是\"加分项\"。"),
        'LLA': ("当学习进展缓慢时，您可能产生\"进步焦虑\"，"
                "请记住：记录\"微进步日志\"，用具体数据对抗模糊的焦虑感。"),
    }
    warning_desc = warning_risks.get(bot_dim,
        "在您的{cn}领域，请注意识别\"虚假进步感\"——忙碌不等于有效，进展顺利不等于方向正确。")
    warning_card = Table([[Paragraph(
        f"针对您的{DIM_CN_NAMES.get(bot_dim,bot_dim)}（{bot_score:.1f}），"
        f"以下是您在\"求进\"路上最容易掉进去的坑：<br/><br/>{warning_desc}",
        ps('WARNING_DESC', fontSize=9, textColor=colors.HexColor('#4a3d35'), leading=13)
    )]], colWidths=[PAGE_W])
    warning_card.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#faf6f4')),
        ('TOPPADDING', (0,0), (-1,-1), 10), ('BOTTOMPADDING', (0,0), (-1,-1), 10),
        ('LEFTPADDING', (0,0), (-1,-1), 14), ('RIGHTPADDING', (0,0), (-1,-1), 14),
        ('LINEABOVE', (0,0), (-1,0), 3, V33_COLOR_SHIELD),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#e0c4ba')),
        ('ROUNDEDCORNERS', [8]),
    ]))
    story.append(warning_card)
    story.append(Spacer(1, 5*mm))
    story.append(Paragraph(
        "让我们通过<b>「两个真实场景」</b>，来看看您的天赋是如何在攻守两端同时发挥作用的。",
        ps('HOOK2', fontSize=9, textColor=V33_COLOR_WARM_GRAY, leading=13, spaceAfter=10)))

    # ═════════════════════════════════════════════
    # 第三阶段：【场景演练】求进与避坑的实战模拟
    # ═════════════════════════════════════════════
    story.append(PageBreak())
    story.append(Paragraph("第三阶段：【场景演练】求进与避坑的实战模拟",
        ps('S3TITLE', fontSize=14, textColor=V33_COLOR_PRIMARY, spaceAfter=5)))
    story.append(Paragraph("Phase 3: Scenario Simulation - Advance vs Defend",
        ps('S3SUB', fontSize=8, textColor=V33_COLOR_WARM_GRAY, spaceAfter=10)))

    # 场景一：求进型
    if top_subs:
        td, ts, tn, tss = top_subs[0]
        scene1_header = Paragraph(
            f"<b>场景一（求进型）：运用{DIM_CN_NAMES.get(td,td)}·{tn}争取资源</b>",
            ps('SCENE1_HEAD', fontSize=11, textColor=colors.HexColor('#5a7a52'), spaceAfter=5))
        story.append(scene1_header)
        s1a_data = [['进攻武器', '维度', '得分', '场景角色'],
                    ['进攻武器', f'{DIM_CN_NAMES.get(td,td)} - {tn}', f'{tss:.1f}', '核心优势']]
        if len(top_subs) > 1:
            td2, ts2, tn2, tss2 = top_subs[1]
            s1a_data.append(['支撑武器', f'{DIM_CN_NAMES.get(td2,td2)} - {tn2}', f'{tss2:.1f}', '协同优势'])
        s1a_table = Table(s1a_data, colWidths=[90, 190, 70, 70])
        s1a_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), V33_COLOR_CHARGE),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('FONTNAME', (0,0), (-1,0), font_name+'-Bold'),
            ('FONTSIZE', (0,0), (-1,0), 9),
            ('FONTNAME', (0,1), (-1,-1), font_name),
            ('FONTSIZE', (0,1), (-1,-1), 9),
            ('BACKGROUND', (0,1), (-1,-1), V33_COLOR_CHARGE_LIGHT),
            ('ALIGN', (0,0), (0,-1), 'CENTER'),
            ('ALIGN', (2,0), (3,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('TOPPADDING', (0,0), (-1,-1), 6), ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ('LEFTPADDING', (0,0), (-1,-1), 8),
            ('ROUNDEDCORNERS', [6, 6, 0, 0]),
            ('LINEBELOW', (0,1), (-1,-2), 0.5, colors.HexColor('#d4e2ce')),
        ]))
        story.append(s1a_table)
        story.append(Spacer(1, 3*mm))
        scene1_desc = _scene_advance(td, ts, tn, tss)
        s1_card = Table([[Paragraph(scene1_desc,
            ps('SCENE1_DESC', fontSize=9, textColor=colors.HexColor('#3d4a35'), leading=13))]],
            colWidths=[PAGE_W])
        s1_card.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#f5f9f4')),
            ('TOPPADDING', (0,0), (-1,-1), 10), ('BOTTOMPADDING', (0,0), (-1,-1), 10),
            ('LEFTPADDING', (0,0), (-1,-1), 14), ('RIGHTPADDING', (0,0), (-1,-1), 14),
            ('LINEABOVE', (0,0), (-1,0), 3, V33_COLOR_CHARGE),
            ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#c8dcc7')),
            ('ROUNDEDCORNERS', [8]),
        ]))
        story.append(s1_card)
        story.append(Spacer(1, 6*mm))

    # 场景二：避坑型
    if bot_subs:
        bd, bs, bn, bss = bot_subs[0]
        scene2_header = Paragraph(
            f"<b>场景二（避坑型）：防御{DIM_CN_NAMES.get(bd,bd)}·{bn}的潜在陷阱</b>",
            ps('SCENE2_HEAD', fontSize=11, textColor=colors.HexColor('#9e6555'), spaceAfter=5))
        story.append(scene2_header)
        intro, behavior = _scene_shield(bd, bs, top_dim, top_score)
        s2a_data = [['防御雷达', '维度', '得分', '场景角色']]
        for row_label, (dim, score, sub_name, sub_score) in [('需保护', bot_subs[0],)]:
            if len(bot_subs) > 0:
                bd2, bs2, bn2, bss2 = bot_subs[0]
                s2a_data.append([row_label, f'{DIM_CN_NAMES.get(bd2,bd2)} - {bn2}', f'{bss2:.1f}', '防护区'])
        if top_subs:
            td3, ts3, tn3, tss3 = top_subs[0]
            s2a_data.append(['协同优势', f'{DIM_CN_NAMES.get(td3,td3)} - {tn3}', f'{tss3:.1f}', '分析工具'])
        s2a_table = Table(s2a_data, colWidths=[90, 190, 70, 70])
        s2a_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), V33_COLOR_SHIELD),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('FONTNAME', (0,0), (-1,0), font_name+'-Bold'),
            ('FONTSIZE', (0,0), (-1,0), 9),
            ('FONTNAME', (0,1), (-1,-1), font_name),
            ('FONTSIZE', (0,1), (-1,-1), 9),
            ('BACKGROUND', (0,1), (-1,-2), colors.HexColor('#faf6f4')),
            ('BACKGROUND', (0,-1), (-1,-1), V33_COLOR_CHARGE_LIGHT),
            ('TEXTCOLOR', (0,1), (0,-2), V33_COLOR_SHIELD),
            ('TEXTCOLOR', (0,-1), (0,-1), V33_COLOR_CHARGE),
            ('ALIGN', (0,0), (0,-1), 'CENTER'),
            ('ALIGN', (2,0), (3,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('TOPPADDING', (0,0), (-1,-1), 6), ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ('LEFTPADDING', (0,0), (-1,-1), 8),
            ('ROUNDEDCORNERS', [6, 6, 0, 0]),
            ('LINEBELOW', (0,1), (-1,-2), 0.5, colors.HexColor('#e0c4ba')),
        ]))
        story.append(s2a_table)
        story.append(Spacer(1, 3*mm))
        s2_card = Table([[Paragraph(behavior,
            ps('SCENE2_DESC', fontSize=9, textColor=colors.HexColor('#4a3d35'), leading=13))]],
            colWidths=[PAGE_W])
        s2_card.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#faf6f4')),
            ('TOPPADDING', (0,0), (-1,-1), 10), ('BOTTOMPADDING', (0,0), (-1,-1), 10),
            ('LEFTPADDING', (0,0), (-1,-1), 14), ('RIGHTPADDING', (0,0), (-1,-1), 14),
            ('LINEABOVE', (0,0), (-1,0), 3, V33_COLOR_SHIELD),
            ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#e0c4ba')),
            ('ROUNDEDCORNERS', [8]),
        ]))
        story.append(s2_card)
        story.append(Spacer(1, 5*mm))

    story.append(Paragraph(
        "看清了攻守两端的实战模式后，让我们来制定<b>「您的专属进化路线图」</b>。",
        ps('HOOK3', fontSize=9, textColor=V33_COLOR_WARM_GRAY, leading=13, spaceAfter=10)))

    # ═════════════════════════════════════════════
    # 第四阶段：【进化指南】优势扩容与安全垫
    # ═════════════════════════════════════════════
    story.append(PageBreak())
    story.append(Paragraph("第四阶段：【进化指南】优势扩容与安全垫",
        ps('S4TITLE', fontSize=14, textColor=V33_COLOR_PRIMARY, spaceAfter=5)))
    story.append(Paragraph("Phase 4: Advantage Expansion & Safety Net - 80%求进 + 20%避坑",
        ps('S4SUB', fontSize=8, textColor=V33_COLOR_WARM_GRAY, spaceAfter=10)))

    # 优势扩容
    story.append(Paragraph(
        f"<b>优势扩容策略（80%篇幅）</b>",
        ps('S4ADV_TITLE', fontSize=11, textColor=colors.HexColor('#5a7a52'), spaceAfter=5)))

    if top_subs:
        s4a_data = [['进化杠杆（≥4.0）', '维度', '得分', '扩容方向']]
        directions = ['从「X」升级为「Y」', '从「执行者」升级为「影响者」', '从「专才」升级为「通才」']
        for i, (dim, score, sub_name, sub_score) in enumerate(top_subs[:2], 1):
            s4a_data.append([f'杠杆{i}', f'{DIM_CN_NAMES.get(dim,dim)} - {sub_name}',
                             f'{sub_score:.1f}', directions[i-1]])
        s4a_table = Table(s4a_data, colWidths=[80, 190, 70, 80])
        s4a_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), V33_COLOR_CHARGE),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('FONTNAME', (0,0), (-1,0), font_name+'-Bold'),
            ('FONTSIZE', (0,0), (-1,0), 9),
            ('FONTNAME', (0,1), (-1,-1), font_name),
            ('FONTSIZE', (0,1), (-1,-1), 9),
            ('BACKGROUND', (0,1), (-1,-1), V33_COLOR_CHARGE_LIGHT),
            ('ALIGN', (0,0), (0,-1), 'CENTER'),
            ('ALIGN', (2,0), (3,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('TOPPADDING', (0,0), (-1,-1), 6), ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ('LEFTPADDING', (0,0), (-1,-1), 8),
            ('ROUNDEDCORNERS', [6, 6, 0, 0]),
            ('LINEBELOW', (0,1), (-1,-2), 0.5, colors.HexColor('#d4e2ce')),
        ]))
        story.append(s4a_table)
        story.append(Spacer(1, 3*mm))

        strategy_text = _strategy_advance(top_dims, top_subs)
        strat_cards_data = [[Paragraph(strategy_text,
            ps('STRAT_TEXT', fontSize=9, textColor=colors.HexColor('#3d4a35'), leading=13))]]
        for seg in strategy_text.split('<br/><br/>'):
            if seg.strip():
                seg_card = Table([[Paragraph(seg,
                    ps('STRAT_SEG', fontSize=9, textColor=colors.HexColor('#3d4a35'), leading=13))]],
                    colWidths=[PAGE_W])
                seg_card.setStyle(TableStyle([
                    ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#f5f9f4')),
                    ('TOPPADDING', (0,0), (-1,-1), 8), ('BOTTOMPADDING', (0,0), (-1,-1), 8),
                    ('LEFTPADDING', (0,0), (-1,-1), 12), ('RIGHTPADDING', (0,0), (-1,-1), 12),
                    ('LINEABOVE', (0,0), (-1,0), 2, V33_COLOR_CHARGE),
                    ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#c8dcc7')),
                    ('ROUNDEDCORNERS', [8]),
                ]))
                story.append(seg_card)
                story.append(Spacer(1, 3*mm))

    story.append(Spacer(1, 4*mm))

    # 安全防御
    story.append(Paragraph(
        f"<b>安全防御策略（20%篇幅）</b>",
        ps('S4SHIELD_TITLE', fontSize=11, textColor=colors.HexColor('#9e6555'), spaceAfter=5)))

    if bot_subs:
        s4b_data = [['防护区数据锚点（<3.0）', '维度', '得分', '防护方式']]
        for (dim, score, sub_name, sub_score) in bot_subs[:2]:
            s4b_data.append(['防护区', f'{DIM_CN_NAMES.get(dim,dim)} - {sub_name}',
                             f'{sub_score:.1f}', '流程/工具代偿'])
        s4b_table = Table(s4b_data, colWidths=[110, 190, 60, 70])
        s4b_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), V33_COLOR_SHIELD),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('FONTNAME', (0,0), (-1,0), font_name+'-Bold'),
            ('FONTSIZE', (0,0), (-1,0), 9),
            ('FONTNAME', (0,1), (-1,-1), font_name),
            ('FONTSIZE', (0,1), (-1,-1), 9),
            ('BACKGROUND', (0,1), (-1,-1), colors.HexColor('#fdf6f0')),
            ('TEXTCOLOR', (0,1), (0,-1), V33_COLOR_SHIELD),
            ('ALIGN', (0,0), (0,-1), 'CENTER'),
            ('ALIGN', (2,0), (3,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('TOPPADDING', (0,0), (-1,-1), 6), ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ('LEFTPADDING', (0,0), (-1,-1), 8),
            ('ROUNDEDCORNERS', [6, 6, 0, 0]),
        ]))
        story.append(s4b_table)
        story.append(Spacer(1, 3*mm))

        safety_text = _safety_text(bot_dims, bot_subs)
        safety_card = Table([[Paragraph(
            f"<b>安全垫策略：不要用意志力硬抗，用流程来规避</b><br/><br/>{safety_text}",
            ps('SAFETY', fontSize=9, textColor=colors.HexColor('#4a3d35'), leading=13))]],
            colWidths=[PAGE_W])
        safety_card.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#faf6f4')),
            ('TOPPADDING', (0,0), (-1,-1), 10), ('BOTTOMPADDING', (0,0), (-1,-1), 10),
            ('LEFTPADDING', (0,0), (-1,-1), 14), ('RIGHTPADDING', (0,0), (-1,-1), 14),
            ('LINEABOVE', (0,0), (-1,0), 2, V33_COLOR_SHIELD),
            ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#e0c4ba')),
            ('ROUNDEDCORNERS', [8]),
        ]))
        story.append(safety_card)
        story.append(Spacer(1, 6*mm))

    # 结语
    conclusion_text = (
        f"<b>进化寄语：攻守兼备，方能行稳致远</b><br/><br/>"
        f"真正的职场强者，不是没有弱点，而是懂得：<br/>"
        f"• 如何利用天赋去<b>创造赢的机会</b>（求进）<br/>"
        f"• 如何用策略守住自己的<b>能量边界</b>（避坑）<br/><br/>"
        f"您的{DIM_CN_NAMES.get(top_dim,top_dim)}（{top_score:.1f}）是您在\"攻\"端的利器，"
        f"而您的{DIM_CN_NAMES.get(bot_dim,bot_dim)}（{bot_score:.1f}）需要您用策略来守护。"
        f"<b>您不需要变成一个没有弱点的人，您需要的是：知道自己的优势在哪里放大，"
        f"弱点在哪里设防。</b>"
    )
    conclusion_card = Table([[Paragraph(conclusion_text,
        ps('CONCL', fontSize=9, textColor=colors.HexColor('#4a3d35'), leading=14))]],
        colWidths=[PAGE_W])
    conclusion_card.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#f8f5f2')),
        ('TOPPADDING', (0,0), (-1,-1), 14), ('BOTTOMPADDING', (0,0), (-1,-1), 14),
        ('LEFTPADDING', (0,0), (-1,-1), 16), ('RIGHTPADDING', (0,0), (-1,-1), 16),
        ('LINEABOVE', (0,0), (-1,0), 3, V33_COLOR_PRIMARY),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#d4c8ba')),
        ('ROUNDEDCORNERS', [10]),
    ]))
    story.append(conclusion_card)
    story.append(Spacer(1, 8*mm))

    ft = Table([[Paragraph("© 2026 Santa Chow 香港求职咨询  |  8维能力测评报告",
                  ps('FTEXT', fontSize=8, textColor=colors.white, alignment=TA_CENTER))]],
               colWidths=[PAGE_W])
    ft.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#8b7355')),
        ('TOPPADDING', (0,0), (-1,-1), 8), ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('ROUNDEDCORNERS', [6]),
    ]))
    story.append(ft)

    # ═════════════════════════════════════════════
    # 附录：8维分数总览
    # ═════════════════════════════════════════════
    story.append(PageBreak())
    story.append(Paragraph("附录：8维能力分数总览",
        ps('APPENDIX_TITLE', fontSize=14, textColor=V33_COLOR_PRIMARY, spaceAfter=5)))
    story.append(Paragraph("Appendix: 8-Dimensional Competency Score Overview",
        ps('APPENDIX_SUB', fontSize=8, textColor=V33_COLOR_WARM_GRAY, spaceAfter=10)))

    app_data = [['维度', '子能力', '得分', '评级', '解读']]
    for dim, subs in sub_scores.items():
        for j, sub in enumerate(subs):
            row_label = DIM_CN_NAMES.get(dim, dim) if j == 0 else ''
            app_data.append([row_label, sub['name'], f"{sub['score']:.1f}",
                             _level_label(sub['score']), _score_tag(sub['score'])])
    app_table = Table(app_data, colWidths=[70, 90, 50, 50, 90])
    app_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#8b7355')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), font_name+'-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 8),
        ('FONTNAME', (0,1), (-1,-1), font_name),
        ('FONTSIZE', (0,1), (-1,-1), 8),
        ('ALIGN', (2,0), (3,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 5), ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('LEFTPADDING', (0,0), (-1,-1), 5),
        ('ROUNDEDCORNERS', [6, 6, 0, 0]),
        ('LINEBELOW', (0,0), (-1,0), 0.5, colors.HexColor('#8b7355')),
        ('LINEBELOW', (0,1), (-1,-1), 0.3, colors.HexColor('#e8e0d8')),
    ]))
    # 为不同评级添加背景色
    for i, row in enumerate(app_data[1:], start=1):
        rating = row[4]
        if '求进武器' in rating:
            app_table.setStyle(TableStyle([
                ('BACKGROUND', (0,i), (-1,i), colors.HexColor('#f5f9f4')),
                ('TEXTCOLOR', (0,i), (0,i), colors.HexColor('#5a7a52')),
            ]))
        elif '防护区' in rating:
            app_table.setStyle(TableStyle([
                ('BACKGROUND', (0,i), (-1,i), colors.HexColor('#fdf6f0')),
                ('TEXTCOLOR', (0,i), (0,i), colors.HexColor('#9e6555')),
            ]))
    story.append(app_table)
    story.append(Spacer(1, 6*mm))

    legend_data = [['评分说明'],
                   ['5分：杰出 — 该能力已形成明显优势，可作为职业发展的核心武器。'],
                   ['4-4.9分：高 — 该能力高于平均水平，具备竞争力，可重点发挥。'],
                   ['3-3.9分：中 — 该能力处于正常范围，有提升空间，建议针对性练习。'],
                   ['2-2.9分：低（防护区）— 该能力低于平均水平，需要通过策略或代偿方式规避风险。']]
    legend_table = Table(legend_data, colWidths=[PAGE_W])
    legend_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#f5f0e8')),
        ('TEXTCOLOR', (0,0), (-1,0), V33_COLOR_PRIMARY),
        ('FONTNAME', (0,0), (-1,0), font_name+'-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 9),
        ('FONTNAME', (0,1), (-1,-1), font_name),
        ('FONTSIZE', (0,1), (-1,-1), 8),
        ('TEXTCOLOR', (0,1), (-1,-1), colors.HexColor('#6b5b4f')),
        ('TOPPADDING', (0,0), (-1,-1), 6), ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 12),
        ('ROUNDEDCORNERS', [6]),
        ('LINEBELOW', (0,0), (-1,0), 0.5, V33_COLOR_PRIMARY),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#d4c8ba')),
    ]))
    story.append(legend_table)
    story.append(Spacer(1, 10*mm))

    ft2 = Table([[Paragraph("© 2026 Santa Chow 香港求职咨询  |  附录：8维能力分数总览",
                  ps('FTEXT2', fontSize=8, textColor=colors.white, alignment=TA_CENTER))]],
               colWidths=[PAGE_W])
    ft2.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#8b7355')),
        ('TOPPADDING', (0,0), (-1,-1), 8), ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('ROUNDEDCORNERS', [6]),
    ]))
    story.append(ft2)

    doc.build(story)
    buffer.seek(0)
    return buffer


# ============ v3.3 报告 API 端点 ============
@app.route('/api/quiz/report_48_v33/<int:result_id>')
def report_48_v33(result_id):
    """生成 v3.3 四阶递进式 PDF 报告"""
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute('SELECT * FROM quiz_results_48 WHERE id = ?', (result_id,))
            row = c.fetchone()

        if not row:
            return jsonify({'error': 'Not found'}), 404

        scores = json.loads(row['scores'])
        answers_raw = row['answers']
        answers = json.loads(answers_raw) if answers_raw else {}
        try:
            q_order_raw = row['question_order']
        except (KeyError, IndexError, TypeError):
            q_order_raw = None
        question_order = json.loads(q_order_raw) if q_order_raw else []

        pdf_buffer = generate_pdf_48_v33(
            result_id, scores, answers, row['user_name'], row['experience'],
            question_order=question_order, font_name=CHINESE_FONT or 'Helvetica'
        )
        report_date = datetime.now().strftime("%Y%m%d")
        return send_file(pdf_buffer, mimetype='application/pdf',
                        as_attachment=True,
                        download_name=f'8d_report_v33_{row["user_name"]}_{report_date}.pdf')
    except Exception as e:
        import traceback
        print(f"V3.3 PDF生成错误: {traceback.format_exc()}")
        return jsonify({'error': str(e), 'font_available': CHINESE_FONT is not None,
                       'font_name': CHINESE_FONT or 'none'}), 500

# ============ 主函数 ============
if __name__ == '__main__':
    app.run(debug=os.environ.get('FLASK_DEBUG', 'false').lower() == 'true',
             host='0.0.0.0', port=PORT, use_reloader=False)
