"""
7维能力测评 - 一体化后端（Flask）
功能：问卷服务 + API + PDF报告 + 批量导入
部署：只需一个Render.com服务
"""

from flask import Flask, request, jsonify, send_file, render_template_string
from flask_cors import CORS
import json
import sqlite3
import os
import io
import csv
from datetime import datetime
from contextlib import contextmanager

# PDF生成
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

app = Flask(__name__)
CORS(app)

# 配置
DATABASE = os.environ.get('DATABASE', 'quiz_results.db')
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
    # 本地候选（开发者机器）
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts', 'NotoSansCJK-Regular.otf'),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts', 'NotoSansCJKsc-Regular.otf'),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts', 'wqy-microhei.ttc'),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts', 'NotoSansCJK-Regular.ttc'),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'NotoSansCJK-Regular.otf'),
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

    # 3. 递归搜索系统字体目录
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

    # 4. 尝试下载（Render 等容器环境）
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
            font = TTFont(name, path)
            pdfmetrics.registerFont(font)
            print(f"✓ 成功注册: {name}")
            print(f"  路径: {path}")
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
        .header{background:linear-gradient(135deg,#1e3a8a 0%,#3b82f6 100%);color:white;padding:20px;border-radius:12px;margin-bottom:20px}
        .header h1{font-size:24px;margin-bottom:5px}
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
        .badge{padding:3px 10px;border-radius:20px;font-size:11px;font-weight:600}
        .badge-green{background:#d1fae5;color:#065f46}
        .badge-red{background:#fee2e2;color:#991b1b}
        .badge-yellow{background:#fef3c7;color:#92400e}
        .btn{padding:8px 14px;background:#1e3a8a;color:white;border:none;border-radius:6px;cursor:pointer;font-size:13px}
        .btn:hover{background:#2563eb}
        .btn-sm{padding:5px 10px;font-size:12px}
        .btn-red{background:#ef4444}.btn-red:hover{background:#dc2626}
        .btn-green{background:#10b981}.btn-green:hover{background:#059669}
        .import-section{border:2px dashed #ddd;padding:30px;text-align:center;border-radius:12px;margin-top:20px}
        .token-input{display:inline-block;padding:8px 12px;border:1px solid #ddd;border-radius:6px;width:120px}
        .msg{background:#f0f9ff;border-left:4px solid #1e3a8a;padding:12px 16px;border-radius:6px;margin:10px 0;font-size:13px}
        .token-list{max-height:300px;overflow-y:auto}
        .hidden{display:none}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>⚙️ 8维能力测评 - 管理后台</h1>
            <p>数据管理 | Token权限 | 报告下载</p>
        </div>
        <div class="tabs">
            <div class="tab active" onclick="showTab('records')">📋 48题记录</div>
            <div class="tab" onclick="showTab('tokens')">🔑 Token管理</div>
            <div class="tab" onclick="showTab('import')">📤 批量导入</div>
        </div>

        <!-- 48题记录 -->
        <div id="tab-records">
            <div class="stats-grid" id="stats48"></div>
            <div class="card">
                <h2>📋 48题测评记录（含Token来源）</h2>
                <div style="margin-bottom:15px;display:flex;gap:10px;flex-wrap:wrap">
                    <input type="text" id="searchName" placeholder="搜索姓名..." style="padding:8px;border:1px solid #ddd;border-radius:6px;width:160px">
                    <select id="filterIndustry48" style="padding:8px;border:1px solid #ddd;border-radius:6px"><option value="">所有行业</option></select>
                    <button class="btn" onclick="load48()">搜索</button>
                    <a href="/api/quiz/list_48" class="btn" style="background:#10b981">📥 导出记录</a>
                </div>
                <div style="overflow-x:auto">
                    <table><thead><tr><th>ID</th><th>姓名</th><th>行业</th><th>年限</th><th>Token</th><th>提交时间</th><th>操作</th></tr></thead><tbody id="table48"></tbody></table>
                </div>
            </div>
        </div>

        <!-- Token管理 -->
        <div id="tab-tokens" class="hidden">
            <div class="stats-grid" id="statsTokens"></div>
            <div class="card">
                <h2>🔑 生成访问Token</h2>
                <div style="display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-bottom:15px">
                    <div>
                        <label style="font-size:12px;color:#888">数量</label><br>
                        <input type="number" id="genCount" value="20" min="1" max="300" class="token-input" style="width:80px">
                    </div>
                    <div>
                        <label style="font-size:12px;color:#888">前缀</label><br>
                        <input type="text" id="genPrefix" value="8D" maxlength="10" class="token-input" style="width:80px">
                    </div>
                    <div>
                        <label style="font-size:12px;color:#888">分配给（选填）</label><br>
                        <input type="text" id="genAssign" placeholder="如：香港大学MBA班" class="token-input" style="width:180px">
                    </div>
                    <div style="margin-top:18px"><button class="btn btn-green" onclick="generateTokens()">生成</button></div>
                </div>
                <div id="genResult" class="msg" style="display:none"></div>
            </div>
            <div class="card">
                <h2>📋 Token列表</h2>
                <div style="margin-bottom:12px;display:flex;gap:10px;flex-wrap:wrap">
                    <select id="filterTokenUsed" onchange="loadTokens()" style="padding:8px;border:1px solid #ddd;border-radius:6px">
                        <option value="">全部</option><option value="0">未使用</option><option value="1">已使用</option>
                    </select>
                    <button class="btn" onclick="loadTokens()">刷新</button>
                    <a href="/api/token/export" class="btn" style="background:#10b981">📥 导出CSV</a>
                </div>
                <div class="token-list" style="overflow-x:auto">
                    <table><thead><tr><th>Token</th><th>状态</th><th>分配给</th><th>创建时间</th><th>使用时间</th><th>操作</th></tr></thead><tbody id="tableTokens"></tbody></table>
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
    <script>
        const API='';
        function showTab(name){
            document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
            document.querySelectorAll('[id^=tab-]').forEach(t=>t.classList.add('hidden'));
            document.getElementById('tab-'+name).classList.remove('hidden');
            event.target.classList.add('active');
        }
        async function load48(){
            const name=document.getElementById('searchName').value;
            const res=await fetch(API+'/api/quiz/list_48?limit=200');
            const data=await res.json();
            document.getElementById('stats48').innerHTML=`
                <div class="stat-box"><div class="stat-value">${data.count}</div><div class="stat-label">48题总记录</div></div>
                <div class="stat-box"><div class="stat-value">${data.results.filter(r=>r.access_token).length}</div><div class="stat-label">Token来源</div></div>`;
            const industries=[...new Set(data.results.map(r=>r.industry||''))].filter(Boolean);
            document.getElementById('filterIndustry48').innerHTML='<option value="">所有行业</option>'+
                industries.map(i=>`<option value="${i}">${i}</option>`).join('');
            const filtered=data.results.filter(r=>!name||(r.user_name||'').includes(name));
            document.getElementById('table48').innerHTML=filtered.map(r=>`
                <tr>
                    <td>${r.id}</td>
                    <td>${r.user_name||'匿名'}</td>
                    <td>${r.industry||'-'}</td>
                    <td>${r.experience||'-'}</td>
                    <td><span style="font-size:11px;color:#1e3a8a;font-family:monospace">${r.access_token||'无'}</span></td>
                    <td>${r.submitted_at ? new Date(r.submitted_at).toLocaleDateString() : '-'}</td>
                    <td><button class="btn btn-sm" onclick="window.open('${API}/api/quiz/report_48/${r.id}','_blank')">PDF</button></td>
                </tr>`).join('') || '<tr><td colspan="7" style="text-align:center;color:#888">暂无记录</td></tr>';
        }
        async function loadTokens(){
            const used=document.getElementById('filterTokenUsed').value;
            const res=await fetch(API+'/api/token/list?used='+used);
            const data=await res.json();
            document.getElementById('statsTokens').innerHTML=`
                <div class="stat-box"><div class="stat-value">${data.total}</div><div class="stat-label">总Token数</div></div>
                <div class="stat-box"><div class="stat-value" style="color:#10b981">${data.available}</div><div class="stat-label">可用</div></div>
                <div class="stat-box"><div class="stat-value" style="color:#ef4444">${data.used}</div><div class="stat-label">已使用</div></div>`;
            document.getElementById('tableTokens').innerHTML=data.tokens.map(t=>`
                <tr>
                    <td><span style="font-family:monospace;font-size:13px">${t.token}</span></td>
                    <td><span class="badge ${t.used?'badge-red':'badge-green'}">${t.used?'已使用':'未使用'}</span></td>
                    <td style="font-size:12px;color:#666">${t.assigned_to||'-'}</td>
                    <td style="font-size:12px">${t.created_at ? new Date(t.created_at).toLocaleDateString() : '-'}</td>
                    <td style="font-size:12px">${t.used_at ? new Date(t.used_at).toLocaleDateString() : '-'}</td>
                    <td>
                        ${!t.used ? `<button class="btn btn-sm" onclick="resetToken('${t.token}')" style="background:#f59e0b">重置</button>` : ''}
                        <button class="btn btn-sm btn-red" onclick="deleteToken('${t.token}')">删除</button>
                    </td>
                </tr>`).join('') || '<tr><td colspan="6" style="text-align:center;color:#888">暂无Token，请先生成</td></tr>';
        }
        async function generateTokens(){
            const count=parseInt(document.getElementById('genCount').value);
            const prefix=document.getElementById('genPrefix').value.trim()||'8D';
            const assigned=document.getElementById('genAssign').value.trim();
            const res=await fetch(API+'/api/token/generate',{method:'POST',headers:{'Content-Type':'application/json'},
                body:JSON.stringify({count,prefix,assigned_to:assigned})});
            const data=await res.json();
            const el=document.getElementById('genResult');
            if(data.success){
                el.style.display='block';
                el.style.background='#d1fae5';
                el.style.borderColor='#065f46';
                el.innerHTML=`<b>成功生成 ${data.created_count} 个Token：</b><br>`+
                    data.tokens.map(t=>`<span style="font-family:monospace;background:#fff;padding:2px 6px;border-radius:4px;margin:2px;display:inline-block;font-size:12px">${t}</span>`).join(' ');
                loadTokens();
            }else{
                el.style.display='block';
                el.style.background='#fee2e2';
                el.innerHTML=`<b style="color:#991b1b">错误：${data.error}</b>`;
            }
        }
        async function deleteToken(token){
            if(!confirm('确认删除 Token: '+token+'？'))return;
            await fetch(API+'/api/token/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token})});
            loadTokens();
        }
        async function resetToken(token){
            if(!confirm('重置 Token: '+token+' 为未使用？'))return;
            await fetch(API+'/api/token/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token})});
            loadTokens();
        }
        async function importCSV(){
            const file=document.getElementById('csvFile').files[0];
            if(!file){alert('请选择CSV文件');return}
            const formData=new FormData();
            formData.append('file',file);
            try{
                const res=await fetch(API+'/api/quiz/batch-import',{method:'POST',body:formData});
                const data=await res.json();
                document.getElementById('importResult').innerHTML=
                    `<b style="color:${data.success?'#065f46':'#991b1b'}">${data.message}</b> 成功: ${data.success_count||0} 失败: ${data.fail_count||0}`;
            }catch(e){document.getElementById('importResult').innerHTML=`<b style="color:#991b1b">导入失败: ${e.message}</b>`}
        }
        load48();
    </script>
</body>
</html>'''

# ============ Token 权限函数 ============
def validate_token(token):
    """验证 token 是否有效，返回 (is_valid, message)"""
    if not token:
        return False, '缺少访问凭证（token），请使用有效链接访问本页面'
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT used FROM access_tokens WHERE token = ?', (token,))
        row = c.fetchone()
        if not row:
            return False, '访问凭证无效，请联系管理员获取正确链接'
        if row['used'] == 1:
            return False, '此访问链接已使用完毕，无法再次提交'
        return True, 'ok'

def consume_token(token):
    """标记 token 为已使用"""
    with get_db() as conn:
        c = conn.cursor()
        c.execute('UPDATE access_tokens SET used=1, used_at=? WHERE token=?',
                  (datetime.now().isoformat(), token))
        conn.commit()

# ============ 数据库函数 ============
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
        # Token 白名单表
        c.execute('''CREATE TABLE IF NOT EXISTS access_tokens (
            token TEXT PRIMARY KEY,
            used INTEGER DEFAULT 0,
            assigned_to TEXT,
            created_at TEXT,
            used_at TEXT)''')

        # 迁移：给 quiz_results_48 添加 access_token 列（如果不存在）
        try:
            c.execute("ALTER TABLE quiz_results_48 ADD COLUMN access_token TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # 列已存在
        conn.commit()

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
    """8维能力评分：每维6题，支持str/int keys"""
    # Normalize keys to int
    normalized = {}
    for k, v in answers.items():
        try:
            normalized[int(k)] = int(v)
        except (ValueError, TypeError):
            continue
    dims = {
        'COG': {'name':'认知能力','q':[1,2,3,4,5,6]},
        'TEC': {'name':'技术掌握','q':[7,8,9,10,11,12]},
        'COM': {'name':'理解表达','q':[13,14,15,16,17,18]},
        'SOC': {'name':'社交技能','q':[19,20,21,22,23,24]},
        'ORG': {'name':'策划执行','q':[25,26,27,28,29,30]},
        'PRS': {'name':'解决问题','q':[31,32,33,34,35,36]},
        'MGT': {'name':'管理技能','q':[37,38,39,40,41,42]},
        'LLA': {'name':'持续学习','q':[43,44,45,46,47,48]}
    }
    scores = {}
    for dim, cfg in dims.items():
        total = sum(normalized.get(q, 0) for q in cfg['q'])
        avg = total / 6
        scores[dim] = {'name': cfg['name'], 'average': round(avg, 2), 'level': get_level(avg)}
    return scores

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
    """生成PDF报告，使用中文字体"""
    buffer = io.BytesIO()

    # 确定使用的字体
    if CHINESE_FONT:
        font_name = CHINESE_FONT
    else:
        raise Exception('中文字体不可用，请联系管理员')

    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=20*mm, bottomMargin=20*mm)
    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(name='ChineseTitle', fontName=font_name, fontSize=20, alignment=1, spaceAfter=20))
    styles.add(ParagraphStyle(name='ChineseText', fontName=font_name, fontSize=10, spaceAfter=8))
    styles.add(ParagraphStyle(name='ChineseCenter', fontName=font_name, fontSize=11, alignment=1))

    story = []
    story.append(Paragraph('7维能力测评报告', styles['ChineseTitle']))
    story.append(Paragraph(f'<b>{user_name}</b> | {industry} | {experience}', styles['ChineseCenter']))
    story.append(Spacer(1, 15*mm))

    data = [['维度', '分数', '等级']]
    for dim, s in scores.items():
        data.append([s['name'], f"{s['average']:.1f}", s['level']])

    table = Table(data, colWidths=[80*mm, 40*mm, 40*mm])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#667eea')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, -1), font_name),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#ddd')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f0f0f0')]),
    ]))
    story.append(table)
    story.append(Spacer(1, 15*mm))

    insights = {
        'COG': '思维敏锐度反映认知与逻辑能力。',
        'TEC': '数字应用力体现技术掌握程度。',
        'COM': '沟通穿透力代表表达与倾听能力。',
        'SOC': '人际连结力显示社交与情绪智商。',
        'ORG': '目标驱动力反映规划与执行能力。',
        'PRS': '应变决策力体现解决问题能力。',
        'MGT': '团队赋能力显示管理与领导潜力。'
    }
    story.append(Paragraph('<b>维度解读</b>', styles['ChineseText']))
    for dim, s in scores.items():
        story.append(Paragraph(f'<b>{s["name"]}</b>：{insights.get(dim, "")} 本次测评{s["level"]}。', styles['ChineseText']))

    story.append(Spacer(1, 20*mm))
    story.append(Paragraph('由 Santa Chow 专业教练提供', styles['ChineseCenter']))
    story.append(Paragraph(f'Report ID: {result_id} | {datetime.now().strftime("%Y-%m-%d")}', styles['ChineseCenter']))

    doc.build(story)
    buffer.seek(0)
    return buffer

# ============ Token 入口页面（简洁美观）============
HTML_GATEWAY = '''<!DOCTYPE html>
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
        .form-group{text-align:left;margin-bottom:24px;}
        label{font-size:14px;font-weight:600;color:#334155;margin-bottom:8px;display:block;}
        input{
            width:100%;padding:14px 16px;font-size:16px;
            border:2px solid #e2e8f0;border-radius:12px;
            outline:none;transition:border-color 0.2s;
            letter-spacing:2px;font-family:monospace;
        }
        input:focus{border-color:#3b82f6;}
        .btn{
            width:100%;padding:16px;background:linear-gradient(135deg,#1e3a8a,#3b82f6);
            color:white;border:none;border-radius:12px;font-size:17px;
            font-weight:600;cursor:pointer;transition:opacity 0.2s;letter-spacing:2px;
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
        <form id="tokenForm" onsubmit="return handleSubmit()">
            <div class="form-group">
                <label for="token">请输入您的访问码</label>
                <input type="text" id="token" name="token" placeholder="例如：8D-001" autocomplete="off" required>
            </div>
            <button type="submit" class="btn">开始答题 →</button>
        </form>
        <p class="note">访问码由 Santa Chow 提供，如有疑问请联系获取</p>
    </div>
    <script>
        function handleSubmit(){
            var t=document.getElementById('token').value.trim();
            if(!t){alert('请输入访问码');return false;}
            window.location.href='/quiz?token='+encodeURIComponent(t);
            return false;
        }
    </script>
</body>
</html>'''

# ============ 路由 ============
@app.route('/')
def index():
    """Token 入口页面"""
    return HTML_GATEWAY

@app.route('/quiz')
def quiz_page():
    """8维能力测评答题页面"""
    token = request.args.get('token', '')
    if not token:
        return HTML_GATEWAY  # 无 token 跳转回入口
    # 读取 quiz HTML，注入 token
    quiz_path = os.path.join(os.path.dirname(__file__), '8d_quiz_48.html')
    try:
        with open(quiz_path, 'r', encoding='utf-8') as f:
            html = f.read()
        # 将 token 注入页面（前端 JS 会从 URL 读取 ?token=）
        return html
    except FileNotFoundError:
        return jsonify({'error': 'Quiz page not found'}), 404

@app.route('/8d_quiz_48.html')
def quiz_legacy():
    """兼容旧链接，自动跳转"""
    token = request.args.get('token', '')
    if token:
        return quiz_page()
    return HTML_GATEWAY

@app.route('/quiz48')
def quiz48():
    """Alias for the quiz"""
    token = request.args.get('token', '')
    if not token:
        return HTML_GATEWAY
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

# ============ 8D 48题新增路由 ============

@app.route('/api/quiz/submit_48', methods=['POST'])
def submit_48():
    """提交48题8维测评（需Token验证）"""
    try:
        data = request.get_json()
        if not data or 'answers' not in data:
            return jsonify({'error': 'Missing answers'}), 400

        # Token 验证
        token = data.get('token', '')
        is_valid, msg = validate_token(token)
        if not is_valid:
            return jsonify({'error': msg, 'token_required': True}), 403

        scores = calculate_scores_48(data['answers'])

        with get_db() as conn:
            c = conn.cursor()
            c.execute('''INSERT INTO quiz_results_48
                (user_name, experience, industry, answers, question_order, scores, submitted_at, ip_address, user_agent, access_token)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (data.get('name', '匿名用户'), data.get('experience', ''),
                 data.get('industry', ''), json.dumps(data.get('answers', {})),
                 json.dumps(data.get('question_order', [])), json.dumps(scores),
                 datetime.now().isoformat(), request.remote_addr,
                 request.headers.get('User-Agent', ''), token))
            result_id = c.lastrowid
            conn.commit()  # 确保INSERT提交

        # 消耗 token（独立连接）
        consume_token(token)

        return jsonify({'success': True, 'result_id': result_id, 'scores': scores})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/quiz/report_48/<int:result_id>')
def report_48(result_id):
    """生成48题PDF报告（不含行业适配分析）"""
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute('SELECT * FROM quiz_results_48 WHERE id = ?', (result_id,))
            row = c.fetchone()

        if not row:
            return jsonify({'error': 'Not found'}), 404

        scores = json.loads(row['scores'])
        pdf_buffer = generate_pdf_48(row['id'], scores, row['user_name'], row['experience'])
        report_date = datetime.now().strftime("%Y%m%d")

        return send_file(pdf_buffer, mimetype='application/pdf',
                        as_attachment=True,
                        download_name=f'8d_report_{row["user_name"]}_{report_date}.pdf')
    except Exception as e:
        import traceback
        print(f"PDF生成错误: {traceback.format_exc()}")
        return jsonify({'error': str(e), 'font_available': CHINESE_FONT is not None, 'font_name': CHINESE_FONT or 'none'}), 500


def generate_pdf_48(result_id, scores, user_name, experience):
    """生成48题PDF报告 — 纯能力分析，不含行业匹配"""
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
    dim_icons = {'COG':'[C]','TEC':'[T]','COM':'[M]','SOC':'[S]','ORG':'[O]','PRS':'[P]','MGT':'[G]','LLA':'[L]'}

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

    # ========== 核心优势（带文字描述）==========
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

    # ========== 发展空间（带文字描述）==========
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

    # ========== 子能力详解（24项）==========
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

# ============ Token 管理 API ============
@app.route('/api/token/generate', methods=['POST'])
def generate_tokens():
    """批量生成 Token（管理员接口）"""
    try:
        data = request.get_json()
        count = min(int(data.get('count', 10)), 500)  # 最多500个
        prefix = data.get('prefix', '8D').upper()
        assigned_to = data.get('assigned_to', '')

        with get_db() as conn:
            c = conn.cursor()
            # 找当前最大序号
            c.execute('SELECT token FROM access_tokens WHERE token LIKE ? ORDER BY token DESC LIMIT 1',
                      (f'{prefix}-%',))
            existing = c.fetchall()
            if existing:
                try:
                    last_num = int(existing[0]['token'].split('-')[-1])
                except ValueError:
                    last_num = 0
            else:
                last_num = 0

            created = []
            for i in range(1, count + 1):
                token = f'{prefix}-{last_num + i:03d}'
                c.execute('''INSERT OR IGNORE INTO access_tokens (token, assigned_to, created_at)
                              VALUES (?, ?, ?)''',
                          (token, assigned_to, datetime.now().isoformat()))
                if c.rowcount > 0:
                    created.append(token)
            conn.commit()

        return jsonify({
            'success': True,
            'created_count': len(created),
            'tokens': created,
            'message': f'成功生成 {len(created)} 个Token'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/token/list')
def list_tokens():
    """列出所有 Token"""
    try:
        used_filter = request.args.get('used', '')  # ''=全部, '0'=未用, '1'=已用
        with get_db() as conn:
            c = conn.cursor()
            if used_filter == '0':
                c.execute('SELECT * FROM access_tokens WHERE used=0 ORDER BY created_at DESC')
            elif used_filter == '1':
                c.execute('SELECT * FROM access_tokens WHERE used=1 ORDER BY used_at DESC')
            else:
                c.execute('SELECT * FROM access_tokens ORDER BY created_at DESC')
            rows = c.fetchall()

        total = len(rows)
        used = sum(1 for r in rows if r['used'] == 1)
        return jsonify({
            'tokens': [dict(r) for r in rows],
            'total': total,
            'used': used,
            'available': total - used
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/token/delete', methods=['POST'])
def delete_token():
    """删除 Token"""
    try:
        data = request.get_json()
        token = data.get('token', '')
        with get_db() as conn:
            c = conn.cursor()
            c.execute('DELETE FROM access_tokens WHERE token=?', (token,))
            conn.commit()
        return jsonify({'success': True, 'deleted': token})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/token/reset', methods=['POST'])
def reset_token():
    """重置 Token（标记为未使用）"""
    try:
        data = request.get_json()
        token = data.get('token', '')
        with get_db() as conn:
            c = conn.cursor()
            c.execute('UPDATE access_tokens SET used=0, used_at=NULL WHERE token=?', (token,))
            conn.commit()
        return jsonify({'success': True, 'reset': token})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/token/export')
def export_tokens():
    """导出 Token 列表为 CSV"""
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute('SELECT * FROM access_tokens ORDER BY created_at DESC')
            rows = c.fetchall()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Token', '状态', '分配给', '创建时间', '使用时间'])
        for r in rows:
            writer.writerow([
                r['token'],
                '已使用' if r['used'] == 1 else '未使用',
                r['assigned_to'] or '',
                r['created_at'] or '',
                r['used_at'] or ''
            ])
        output.seek(0)
        return send_file(io.BytesIO(output.getvalue().encode('utf-8-sig')),
                        mimetype='text/csv',
                        as_attachment=True,
                        download_name=f'access_tokens_{datetime.now().strftime("%Y%m%d")}.csv')
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============ 48题列表（含Token来源）============
@app.route('/api/quiz/list_48')
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
def admin_init_db():
    """手动初始化数据库（创建所有表）"""
    init_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in c.fetchall()]
    return jsonify({'success': True, 'tables': tables, 'message': '数据库初始化完成'})

# ============ 主函数 ============
if __name__ == '__main__':
    init_db()
    # debug=True 会在文件变化时自动重载（开发用）；但注意debug模式的重载器
    # 会在子进程中执行实际请求，可能导致数据库文件句柄问题。
    # Render部署时会用 gunicorn，不会遇到此问题。
    app.run(debug=os.environ.get('FLASK_DEBUG', 'false').lower() == 'true',
             host='0.0.0.0', port=PORT, use_reloader=False)
