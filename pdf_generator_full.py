"""
PDF生成模块 - 完整版 - 跟用户端一模一样
使用Playwright生成完整的四阶段报告PDF
直接复用 8d_quiz_48.html 的渲染逻辑
"""
from io import BytesIO
from playwright.sync_api import sync_playwright
import tempfile
import os
import json

def generate_pdf_48_full(row):
    """
    生成完整PDF - 跟用户端一模一样
    直接复用 8d_quiz_48.html 的渲染逻辑
    支持 sqlite3.Row 对象或字典
    """
    # 兼容 sqlite3.Row 和字典
    if isinstance(row, dict):
        result_id = row.get('id')
        scores = json.loads(row.get('scores', '{}'))
        answers = json.loads(row.get('answers', '{}')) if row.get('answers') else {}
        user_name = row.get('user_name') or '匿名用户'
        industry = row.get('industry') or ''
        experience = row.get('experience') or ''
    else:
        # 假设是 sqlite3.Row 或支持字典式访问的对象
        result_id = row['id']
        scores = json.loads(row['scores'])
        answers = json.loads(row['answers']) if row['answers'] else {}
        user_name = row['user_name'] or '匿名用户'
        industry = row['industry'] or ''
        experience = row['experience'] or ''
    
    # 读取 8d_quiz_48.html 的完整内容
    with open('8d_quiz_48.html', 'r', encoding='utf-8') as f:
        html = f.read()
    
    # 修改HTML，让它直接显示结果（不显示测评界面）
    # 1. 隐藏 infoSection 和 quizSection
    html = html.replace('<div class="card" id="infoSection">', 
                        '<div class="card hidden" id="infoSection">')
    html = html.replace('<div class="card hidden" id="quizSection">', 
                        '<div class="card hidden" id="quizSection">')
    html = html.replace('<div class="card hidden" id="resultSection">', 
                        '<div class="card" id="resultSection">')
    
    # 2. 嵌入分数数据，并直接调用 renderResult()
    scores_js = json.dumps(scores, ensure_ascii=False)
    answers_js = json.dumps(answers, ensure_ascii=False)
    
    script = f'''
    <script>
        // 覆盖 submitQuiz 函数，直接渲染结果
        window.submitQuiz = function() {{
            document.getElementById('quizSection').classList.add('hidden');
            document.getElementById('resultSection').classList.remove('hidden');
            
            // 设置全局变量
            window.scores = {scores_js};
            window.answers = {answers_js};
            window.userName = '{user_name}';
            window.industry = '{industry}';
            window.experience = '{experience}';
            
            // 调用 renderResult（这个函数已经定义在 8d_quiz_48.html 中）
            renderResult(window.scores, window.userName, window.experience, window.industry);
        }};
        
        // 页面加载完成后自动调用
        window.addEventListener('DOMContentLoaded', function() {{
            window.submitQuiz();
        }});
    </script>
    '''
    
    # 将 script 插入到 </body> 前
    html = html.replace('</body>', script + '</body>')
    
    # 将 HTML 保存到临时文件
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.html', delete=False) as f:
        f.write(html)
        temp_html_path = f.name
    
    try:
        # 使用 Playwright 生成 PDF
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            # 加载HTML文件
            page.goto(f'file://{temp_html_path}', wait_until='networkidle', timeout=30000)
            
            # 等待渲染完成（等待结果内容出现）
            page.wait_for_selector('#resultContent', timeout=10000)
            
            # 额外等待2秒，确保所有JS执行完毕
            page.wait_for_timeout(2000)
            
            # 生成PDF
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
