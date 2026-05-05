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

# ============ 中文字体注册 ============
def register_fonts():
    """注册中文字体，支持PDF中文输出"""
    font_paths = [
        # Linux服务器常见字体路径
        '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
        '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/truetype/arphic/uming.ttc',
        '/usr/share/fonts/truetype/arphic/ukai.ttc',
        # macOS
        '/System/Library/Fonts/PingFang.ttc',
        # Windows
        'C:/Windows/Fonts/simhei.ttf',
        'C:/Windows/Fonts/simsun.ttc',
    ]

    for path in font_paths:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont('ChineseFont', path))
                print(f"成功注册字体: {path}")
                return True
            except Exception as e:
                print(f"字体注册失败 {path}: {e}")
                continue

    # 如果都找不到，使用内置Helvetica（会有问题）
    print("警告: 未找到中文字体，PDF中文可能显示异常")
    return False

CHINESE_FONT_AVAILABLE = register_fonts()

# ============ HTML模板（完整问卷页面 - 简体中文）============
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
    <a href="/admin" class="admin-link">⚙️ 管理后台</a>
    <script>
        const API='';
        const DIM_NAMES={COG:'思维敏锐度',TEC:'数字应用力',COM:'沟通穿透力',SOC:'人际连结力',ORG:'目标驱动力',PRS:'应变决策力',MGT:'团队赋能力'};
        let currentQ=0,questionOrder=[],answers={},resultId=null;
        const questions=[
            {id:1,text:'我能快速理解新事物的核心原理',dim:'COG'},
            {id:2,text:'面对复杂问题时，我能迅速找到关键脉络',dim:'COG'},
            {id:3,text:'我善于总结归纳，能把复杂信息简化',dim:'COG'},
            {id:4,text:'我对数据和逻辑敏感，能理性分析',dim:'COG'},
            {id:5,text:'我能熟练使用各种数字工具提升效率',dim:'TEC'},
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
            {id:29,text:'（此题请选择"普通"）测试认真度',dim:'V'},
            {id:30,text:'（此题请选择"普通"）测试稳定性',dim:'V'},
            {id:31,text:'（此题请选择第3项）注意力检验',dim:'V'}
        ];
        const opts=['非常不同意','不同意','普通','同意','非常同意'];

        function shuffleQuestions(){questionOrder=[...Array(28).keys()].sort(()=>Math.random()-0.5).map(i=>i)}

        function startQuiz(){
            const industry=document.getElementById('industry').value;
            const experience=document.getElementById('experience').value;
            if(!industry||!experience){alert('请填写必填项');return}
            sessionStorage.setItem('industry',industry);
            sessionStorage.setItem('experience',experience);
            sessionStorage.setItem('userName',document.getElementById('userName').value);
            shuffleQuestions();
            document.getElementById('info-section').classList.add('hidden');
            document.getElementById('quiz-section').classList.remove('hidden');
            renderQuestion();
        }

        function renderQuestion(){
            const qIdx=questionOrder[currentQ];
            const q=questions[qIdx];
            document.getElementById('progress').style.width=((currentQ+1)/28*100)+'%';
            document.getElementById('prevBtn').style.visibility=currentQ>0?'visible':'hidden';
            document.getElementById('nextBtn').textContent=currentQ<27?'下一题 →':'提交测评 ✓';
            document.getElementById('question-container').innerHTML=`
                <div class="question">
                    <div class="question-meta">第 ${currentQ+1} / 28 题 | ${DIM_NAMES[q.dim]||'效度题'}</div>
                    <div class="question-text">${q.text}</div>
                    <div class="options">${opts.map((o,i)=>`<div class="option"><input type="radio" name="answer" id="opt${i}" value="${i+1}" ${answers[q.id]==i+1?'checked':''}><label for="opt${i}">${o}</label></div>`).join('')}</div>
                </div>`;
        }

        function nextQuestion(){
            const selected=document.querySelector('input[name="answer"]:checked');
            if(!selected){alert('请选择一个选项');return}
            const qIdx=questionOrder[currentQ];
            answers[questions[qIdx].id]=parseInt(selected.value);
            if(currentQ<27){currentQ++;renderQuestion()}else{submitQuiz()}
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
                let html='<div class="score-grid">';
                Object.entries(data.scores).forEach(([dim,s])=>{html+=`<div class="score-item"><div class="score-label">${s.name}</div><div class="score-value">${s.average.toFixed(1)}</div><div class="score-level">${s.level}</div></div>`});
                html+='</div>';
                document.getElementById('scores-display').innerHTML=html;
            }catch(e){alert('提交失败: '+e.message)}
        }

        async function downloadReport(){if(resultId)window.open(API+'/api/quiz/report/'+resultId,'_blank')}

        function resetQuiz(){currentQ=0;answers={};resultId=null;document.getElementById('result-section').classList.add('hidden');document.getElementById('info-section').classList.remove('hidden')}
    </script>
</body>
</html>'''

# ============ 管理后台HTML（简体中文） ============
HTML_ADMIN = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>管理后台 | 7维能力测评</title>
    <style>
        *{box-sizing:border-box;margin:0;padding:0}
        body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#f5f7fa;color:#333}
        .container{max-width:1200px;margin:0 auto;padding:20px}
        .header{background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:white;padding:20px;border-radius:12px;margin-bottom:20px}
        .header h1{font-size:24px;margin-bottom:5px}
        .card{background:white;border-radius:12px;padding:20px;margin-bottom:20px;box-shadow:0 2px 10px rgba(0,0,0,0.05)}
        .card h2{font-size:16px;color:#667eea;margin-bottom:15px;padding-bottom:10px;border-bottom:2px solid #667eea}
        .stats-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:15px;margin-bottom:20px}
        .stat-box{background:#f8f9fc;padding:20px;border-radius:10px;text-align:center}
        .stat-value{font-size:32px;font-weight:700;color:#667eea}
        .stat-label{font-size:12px;color:#888;margin-top:5px}
        table{width:100%;border-collapse:collapse}
        th,td{padding:12px;text-align:left;border-bottom:1px solid #eee}
        th{background:#f8f9fc;font-weight:600;color:#667eea}
        tr:hover{background:#f8f9fc}
        .badge{padding:4px 8px;border-radius:12px;font-size:11px}
        .badge-valid{background:#d4edda;color:#155724}
        .badge-invalid{background:#f8d7da;color:#721c24}
        .btn{padding:8px 16px;background:#667eea;color:white;border:none;border-radius:6px;cursor:pointer}
        .btn:hover{background:#5a6fd6}
        .import-section{border:2px dashed #ddd;padding:30px;text-align:center;border-radius:12px;margin-top:20px}
        .import-section input{margin:10px 0}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>⚙️ 7维能力测评 - 管理后台</h1>
            <p>数据管理 | 批量导入 | 报告下载</p>
        </div>
        <div class="stats-grid" id="stats"></div>
        <div class="card">
            <h2>📋 测评记录</h2>
            <div style="margin-bottom:15px">
                <input type="text" id="searchName" placeholder="搜索姓名..." style="padding:8px;border:1px solid #ddd;border-radius:6px;width:200px">
                <select id="filterIndustry" style="padding:8px;border:1px solid #ddd;border-radius:6px"><option value="">所有行业</option></select>
                <button class="btn" onclick="loadData()">搜索</button>
                <a href="/api/quiz/export" class="btn" style="background:#27ae60;margin-left:10px">📥 导出CSV</a>
            </div>
            <div style="overflow-x:auto">
                <table><thead><tr><th>ID</th><th>姓名</th><th>行业</th><th>年限</th><th>提交时间</th><th>有效性</th><th>操作</th></tr></thead><tbody id="tableBody"></tbody></table>
            </div>
        </div>
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
        <div style="text-align:center;margin-top:20px"><a href="/" style="color:#667eea">← 返回首页</a></div>
    </div>
    <script>
        const API='';
        async function loadStats(){
            const res=await fetch(API+'/api/quiz/all?limit=1000');
            const data=await res.json();
            const valid=data.results.filter(r=>r.validity_check).length;
            document.getElementById('stats').innerHTML=`
                <div class="stat-box"><div class="stat-value">${data.results.length}</div><div class="stat-label">总记录数</div></div>
                <div class="stat-box"><div class="stat-value">${valid}</div><div class="stat-label">有效记录</div></div>
                <div class="stat-box"><div class="stat-value">${data.results.length-valid}</div><div class="stat-label">无效记录</div></div>
                <div class="stat-box"><div class="stat-value">${data.industries.length}</div><div class="stat-label">行业数</div></div>`;
            const industries=[...new Set(data.results.map(r=>r.industry))];
            document.getElementById('filterIndustry').innerHTML='<option value="">所有行业</option>'+industries.map(i=>`<option value="${i}">${i}</option>`).join('');
            renderTable(data.results);
        }
        function renderTable(results){
            document.getElementById('tableBody').innerHTML=results.slice(0,100).map(r=>`
                <tr>
                    <td>${r.id}</td><td>${r.user_name}</td><td>${r.industry}</td><td>${r.experience}</td>
                    <td>${new Date(r.submitted_at).toLocaleDateString()}</td>
                    <td><span class="badge ${r.validity_check?'badge-valid':'badge-invalid'}">${r.validity_check?'有效':'无效'}</span></td>
                    <td><button class="btn" onclick="window.open('${API}/api/quiz/report/${r.id}','_blank')">PDF</button></td>
                </tr>`).join('');
        }
        async function loadData(){
            const name=document.getElementById('searchName').value;
            const industry=document.getElementById('filterIndustry').value;
            const res=await fetch(API+`/api/quiz/all?name=${name}&industry=${industry}&limit=100`);
            const data=await res.json();
            renderTable(data.results);
        }
        async function importCSV(){
            const file=document.getElementById('csvFile').files[0];
            if(!file){alert('请选择CSV文件');return}
            const formData=new FormData();
            formData.append('file',file);
            try{
                const res=await fetch(API+'/api/quiz/batch-import',{method:'POST',body:formData});
                const data=await res.json();
                document.getElementById('importResult').innerHTML=`<b style="color:${data.success?'green':'red'}">${data.message}</b> 成功: ${data.success_count||0} 失败: ${data.fail_count||0}`;
                if(data.success)loadStats();
            }catch(e){document.getElementById('importResult').innerHTML=`<b style="color:red">导入失败: ${e.message}</b>`}
        }
        loadStats();
    </script>
</body>
</html>'''

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

def get_level(score):
    if score >= 4.5: return '优秀'
    elif score >= 3.5: return '良好'
    elif score >= 2.5: return '中等'
    elif score >= 1.5: return '待提升'
    else: return '需改进'

def check_validity(answers):
    q29 = answers.get('q29', 0)
    q30 = answers.get('q30', 0)
    q31 = answers.get('q31', 0)
    return {'is_valid': (q31 == 3) and (q29 <= 2 or q30 <= 2)}

def generate_pdf(result_id, scores, user_name, industry, experience):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=20*mm, bottomMargin=20*mm)
    styles = getSampleStyleSheet()

    # 根据字体可用性选择字体
    if CHINESE_FONT_AVAILABLE:
        font_name = 'ChineseFont'
    else:
        font_name = 'Helvetica'
        print("警告: 使用Helvetica字体，中文可能无法正确显示")

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
        ('FONTNAME', (0, 0), (-1, 0), font_name),
        ('FONTNAME', (0, 1), (-1, -1), font_name),
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

# ============ 路由 ============
@app.route('/')
def index():
    return HTML_INDEX

@app.route('/admin')
def admin():
    return HTML_ADMIN

@app.route('/api/health')
def health():
    return jsonify({'status': 'ok', 'timestamp': datetime.now().isoformat()})

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
        return jsonify({'error': str(e)}), 500

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

# ============ 主函数 ============
if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=PORT)
