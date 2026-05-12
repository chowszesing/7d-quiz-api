"""
PDF生成模块 - 使用Playwright生成跟用户端一模一样的PDF
最后更新: 2026-05-12 09:12
"""
from io import BytesIO
import tempfile
import os
import json
from datetime import datetime

# 惰性导入 - 避免顶层导入导致Gunicorn启动失败
# from playwright.sync_api import sync_playwright  # 移到函数内部

def generate_pdf_48_playwright(row):
    """
    使用 Playwright 生成 PDF - 跟用户端看到的结果页面一模一样
    支持 sqlite3.Row 对象或字典
    """
    # 在函数内部导入 playwright，避免顶层导入失败导致服务器无法启动
    from playwright.sync_api import sync_playwright
    # 兼容 sqlite3.Row 和字典
    if isinstance(row, dict):
        scores = json.loads(row.get('scores', '{}'))
        answers = json.loads(row.get('answers', '{}')) if row.get('answers') else {}
        user_name = row.get('user_name') or '匿名用户'
        industry = row.get('industry') or ''
        experience = row.get('experience') or ''
    else:
        # 假设是 sqlite3.Row 或支持字典式访问的对象
        scores = json.loads(row['scores'])
        answers = json.loads(row['answers']) if row['answers'] else {}
        user_name = row['user_name'] or '匿名用户'
        industry = row['industry'] or ''
        experience = row['experience'] or ''
    
    # 创建 HTML 内容（跟用户端结果页面一模一样）
    html_content = create_result_html(user_name, industry, experience, scores, answers)
    
    # 将 HTML 保存到临时文件
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.html', delete=False) as f:
        f.write(html_content)
        temp_html_path = f.name
    
    try:
        # 使用 Playwright 生成 PDF
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f'file://{temp_html_path}')
            pdf_bytes = page.pdf(
                format='A4',
                print_background=True,
                margin={'top': '10mm', 'bottom': '10mm', 'left': '10mm', 'right': '10mm'}
            )
            browser.close()
        
        # 将 PDF 字节写入 BytesIO
        buffer = BytesIO(pdf_bytes)
        buffer.seek(0)
        return buffer
    finally:
        # 清理临时文件
        if os.path.exists(temp_html_path):
            os.unlink(temp_html_path)


def create_result_html(user_name, industry, experience, scores, answers):
    """创建跟用户端结果页面一模一样的 HTML"""
    # 维度中文名称
    dim_cn = {
        'COG': '认知能力', 'TEC': '技术掌握', 'COM': '理解表达',
        'SOC': '社交技能', 'ORG': '策划执行', 'PRS': '解决问题',
        'MGT': '管理技能', 'LLA': '持续学习'
    }
    
    # 生成分数卡片 HTML
    score_cards = ''
    for dim, data in scores.items():
        dim_name = dim_cn.get(dim, dim)
        score = data['average']
        level = data['level']
        score_cards += f'''
        <div class="score-item">
            <div class="score-label">{dim_name}</div>
            <div class="score-value">{score:.1f}</div>
            <div class="score-level">{level}</div>
        </div>
        '''
    
    # 生成 HTML（简化版，跟用户端类似）
    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>8维能力测评报告 - {user_name}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
            background: #f5f7fa;
            color: #333;
            padding: 20px;
        }}
        .container {{
            max-width: 800px;
            margin: 0 auto;
            background: white;
            border-radius: 16px;
            padding: 30px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.05);
        }}
        .header {{
            text-align: center;
            margin-bottom: 30px;
            padding-bottom: 20px;
            border-bottom: 2px solid #667eea;
        }}
        .header h1 {{
            font-size: 28px;
            color: #667eea;
            margin-bottom: 10px;
        }}
        .header p {{
            color: #888;
            font-size: 14px;
        }}
        .score-grid {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 15px;
            margin: 20px 0;
        }}
        .score-item {{
            background: #f8f9fc;
            padding: 15px;
            border-radius: 10px;
            text-align: center;
        }}
        .score-label {{
            font-size: 12px;
            color: #888;
            margin-bottom: 5px;
        }}
        .score-value {{
            font-size: 24px;
            font-weight: 700;
            color: #667eea;
        }}
        .score-level {{
            font-size: 11px;
            color: #666;
            margin-top: 3px;
        }}
        .section {{
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #eee;
        }}
        .section-title {{
            font-size: 18px;
            font-weight: 600;
            margin-bottom: 15px;
            color: #667eea;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>✨ 8维能力测评报告</h1>
            <p>{user_name} | {industry} | {experience}</p>
            <p>生成时间：{datetime.now().strftime("%Y年%m月%d日")}</p>
        </div>
        
        <div class="section">
            <div class="section-title">📊 维度得分</div>
            <div class="score-grid">
                {score_cards}
            </div>
        </div>
        
        <div class="section">
            <div class="section-title">📈 总结</div>
            <p>您的8维能力测评已完成，报告显示如上。</p>
            <p>详细的人格画像、张力分析和缺陷重塑报告请参考完整版。</p>
        </div>
    </div>
</body>
</html>'''
    
    return html

def generate_image_48_playwright(row, format='png'):
    """
    使用 Playwright 生成图片 - 跟用户端看到的结果页面一模一样
    支持 PNG 和 JPG 格式
    """
    from playwright.sync_api import sync_playwright
    
    # 兼容 sqlite3.Row 和字典
    if isinstance(row, dict):
        scores = json.loads(row.get('scores', '{}'))
        answers = json.loads(row.get('answers', '{}')) if row.get('answers') else {}
        user_name = row.get('user_name') or '匿名用户'
        industry = row.get('industry') or ''
        experience = row.get('experience') or ''
    else:
        scores = json.loads(row['scores'])
        answers = json.loads(row['answers']) if row['answers'] else {}
        user_name = row['user_name'] or '匿名用户'
        industry = row['industry'] or ''
        experience = row['experience'] or ''
    
    # 创建 HTML 内容
    html_content = create_result_html(user_name, industry, experience, scores, answers)
    
    # 将 HTML 保存到临时文件
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.html', delete=False) as f:
        f.write(html_content)
        temp_html_path = f.name
    
    try:
        # 使用 Playwright 截图
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f'file://{temp_html_path}')
            
            # 等待页面渲染完成
            page.wait_for_load_state('networkidle')
            
            # 截图选项
            screenshot_options = {
                'full_page': True,
                'type': format  # 'png' or 'jpeg'
            }
            
            if format == 'jpeg':
                screenshot_options['quality'] = 85
            
            image_bytes = page.screenshot(**screenshot_options)
            browser.close()
        
        # 将图片字节写入 BytesIO
        buffer = BytesIO(image_bytes)
        buffer.seek(0)
        return buffer
    finally:
        # 清理临时文件
        if os.path.exists(temp_html_path):
            os.unlink(temp_html_path)
