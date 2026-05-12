"""
PDF生成模块 - 完整版 - 跟用户端一模一样
使用Playwright生成完整的四阶段报告PDF
"""
from io import BytesIO
from playwright.sync_api import sync_playwright
import tempfile
import os
import json
import subprocess

def generate_pdf_48_full(row):
    """
    生成完整PDF - 包含四阶段报告、雷达图、人格分析等
    支持 sqlite3.Row 对象或字典
    """
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
    
    # 读取模板文件
    html_template = _build_complete_html(scores, answers, user_name, industry, experience)
    
    # 将 HTML 保存到临时文件
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.html', delete=False) as f:
        f.write(html_template)
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


def _build_complete_html(scores, answers, user_name, industry, experience):
    """构建完整的HTML页面，包含四阶段报告"""
    
    # 读取 report_engine_data.js 内容
    try:
        with open('report_engine_data.js', 'r', encoding='utf-8') as f:
            engine_data_js = f.read()
    except:
        engine_data_js = '// Engine data not available'
    
    # 读取 report_engine.js 内容
    try:
        with open('report_engine.js', 'r', encoding='utf-8') as f:
            engine_js = f.read()
    except:
        engine_js = '// Engine not available'
    
    # 构建分数数据（传递给JS）
    scores_js = json.dumps(scores, ensure_ascii=False)
    answers_js = json.dumps(answers, ensure_ascii=False)
    
    # 完整HTML模板
    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>8维能力测评报告 - {user_name}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        :root {{
            --primary: #8b7355;
            --primary-light: #a89070;
            --secondary: #9e948a;
            --accent: #c4907a;
            --dark: #3d3530;
            --light: #faf8f5;
            --success: #7d9b76;
            --gray-200: #e8e0d8;
            --gray-400: #9e948a;
            --charge: #7d9b76;
            --charge-light: #f5f9f4;
            --shield: #c4907a;
            --shield-light: #faf6f4;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Microsoft YaHei", "Noto Sans SC", "PingFang SC", sans-serif;
            background: white;
            color: var(--dark);
            padding: 20px;
        }}
        .card {{
            max-width: 800px;
            width: 100%;
            margin: 0 auto;
            background: white;
            border-radius: 16px;
            padding: 30px;
        }}
        
        /* 结果头部 */
        .result-header {{
            text-align: center;
            padding: 10px 0 20px;
        }}
        .result-header h2 {{
            font-size: 28px;
            color: var(--dark);
        }}
        .result-header p {{
            color: var(--gray-400);
            margin-top: 8px;
            font-size: 13px;
        }}
        
        /* 四阶段通用样式 */
        .phase-section {{
            margin: 20px 0;
        }}
        .phase-title {{
            font-size: 15px;
            font-weight: 700;
            color: var(--primary);
            margin-bottom: 5px;
        }}
        .phase-subtitle {{
            font-size: 11px;
            color: var(--gray-400);
            margin-bottom: 14px;
        }}
        
        /* 情绪天气预报 */
        .weather-card {{
            background: #fdf8f3;
            border-radius: 14px;
            padding: 14px 16px;
            border: 1.5px solid var(--accent);
            margin-bottom: 14px;
        }}
        .weather-title {{
            font-size: 13px;
            font-weight: 700;
            color: #5d4e37;
            margin-bottom: 4px;
        }}
        .weather-text {{
            font-size: 12px;
            color: #7d6e5a;
            line-height: 1.5;
        }}
        
        /* 数据锚点表 */
        .anchor-table {{
            width: 100%;
            border-collapse: separate;
            border-spacing: 0;
            margin-bottom: 12px;
        }}
        .anchor-table th {{
            padding: 8px 10px;
            font-size: 11px;
            font-weight: 700;
            color: white;
            border-radius: 8px 8px 0 0;
        }}
        .anchor-table td {{
            padding: 7px 10px;
            font-size: 12px;
            border-bottom: 1px solid #e8e0d8;
        }}
        
        /* 求进卡 */
        .charge-card {{
            background: var(--charge-light);
            border-radius: 14px;
            padding: 14px 16px;
            margin-bottom: 12px;
            border-top: 3px solid var(--charge);
            border-left: 1px solid #d4e2ce;
            border-right: 1px solid #d4e2ce;
            border-bottom: 1px solid #d4e2ce;
        }}
        .charge-card .card-title {{
            font-size: 13px;
            font-weight: 700;
            color: #5a7a52;
            margin-bottom: 8px;
        }}
        .charge-card .card-content {{
            font-size: 12px;
            color: #3d4a35;
            line-height: 1.6;
        }}
        .charge-card .card-content p {{
            margin-bottom: 6px;
        }}
        .charge-card .card-content ul {{
            list-style: none;
            padding: 0;
        }}
        .charge-card .card-content li {{
            padding: 3px 0 3px 16px;
            position: relative;
        }}
        .charge-card .card-content li::before {{
            content: '•';
            position: absolute;
            left: 0;
            color: var(--charge);
            font-weight: bold;
        }}
        
        /* 避坑卡 */
        .shield-card {{
            background: var(--shield-light);
            border-radius: 14px;
            padding: 14px 16px;
            margin-bottom: 12px;
            border-top: 3px solid var(--shield);
            border-left: 1px solid #e0c4ba;
            border-right: 1px solid #e0c4ba;
            border-bottom: 1px solid #e0c4ba;
        }}
        .shield-card .card-title {{
            font-size: 13px;
            font-weight: 700;
            color: #9e6555;
            margin-bottom: 8px;
        }}
        .shield-card .card-content {{
            font-size: 12px;
            color: #4a3d35;
            line-height: 1.6;
        }}
        .shield-card .card-content p {{
            margin-bottom: 6px;
        }}
        .shield-card .card-content ul {{
            list-style: none;
            padding: 0;
        }}
        .shield-card .card-content li {{
            padding: 3px 0 3px 16px;
            position: relative;
        }}
        .shield-card .card-content li::before {{
            content: '•';
            position: absolute;
            left: 0;
            color: var(--shield);
            font-weight: bold;
        }}
        
        /* 逻辑钩子 */
        .hook-text {{
            font-size: 12px;
            color: var(--gray-400);
            line-height: 1.5;
            margin: 12px 0;
            font-style: italic;
        }}
        
        /* 场景演练 */
        .scenario-card {{
            margin-bottom: 14px;
        }}
        .scenario-header {{
            font-size: 13px;
            font-weight: 700;
            margin-bottom: 8px;
            padding: 8px 12px;
            border-radius: 8px 8px 0 0;
        }}
        .scenario-header.charge {{
            background: var(--charge-light);
            color: #5a7a52;
            border-left: 3px solid var(--charge);
        }}
        .scenario-header.shield {{
            background: var(--shield-light);
            color: #9e6555;
            border-left: 3px solid var(--shield);
        }}
        
        /* 策略卡 */
        .strategy-card {{
            background: var(--charge-light);
            border-radius: 14px;
            padding: 14px 16px;
            margin-bottom: 10px;
            border-top: 2px solid var(--charge);
        }}
        
        /* 安全垫卡 */
        .safety-card {{
            background: var(--shield-light);
            border-radius: 14px;
            padding: 14px 16px;
            margin-bottom: 10px;
            border-top: 2px solid var(--shield);
        }}
        
        /* 结语卡 */
        .closing-card {{
            background: #f8f5f2;
            border-radius: 14px;
            padding: 16px;
            margin-top: 16px;
            border-top: 3px solid var(--primary);
        }}
        .closing-title {{
            font-size: 14px;
            font-weight: 700;
            color: var(--primary);
            margin-bottom: 8px;
        }}
        .closing-text {{
            font-size: 12px;
            color: #5d4e37;
            line-height: 1.7;
        }}
        
        /* 附录 - 8维总览 */
        .appendix-section {{
            margin-top: 20px;
        }}
        .appendix-title {{
            font-size: 15px;
            font-weight: 700;
            color: var(--primary);
            margin-bottom: 12px;
        }}
        .score-overview {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 8px;
            margin-bottom: 14px;
        }}
        .score-overview-item {{
            background: var(--light);
            border-radius: 10px;
            padding: 10px;
            text-align: center;
            border: 1px solid var(--gray-200);
        }}
        .score-overview-item .icon {{
            font-size: 20px;
        }}
        .score-overview-item .name {{
            font-size: 11px;
            color: var(--secondary);
            margin: 4px 0 2px;
        }}
        .score-overview-item .score {{
            font-size: 18px;
            font-weight: 700;
            margin: 2px 0;
        }}
        .score-overview-item.high-light {{
            background: var(--charge-light);
            border-color: #c8dcc7;
        }}
        .score-overview-item.low-light {{
            background: #fdf6f0;
            border-color: #e0c4ba;
        }}
        
        /* 评分说明 */
        .legend-card {{
            background: #f5f0e8;
            border-radius: 10px;
            padding: 12px 14px;
            margin-top: 12px;
        }}
        .legend-title {{
            font-size: 12px;
            font-weight: 700;
            color: var(--primary);
            margin-bottom: 6px;
        }}
        .legend-item {{
            font-size: 11px;
            color: #6b5b4f;
            padding: 3px 0;
        }}
        
        /* 页脚 */
        .result-footer {{
            margin-top: 20px;
            padding: 10px;
            background: var(--primary);
            border-radius: 10px;
            text-align: center;
            font-size: 12px;
            color: white;
        }}
        
        /* 免责声明 */
        .disclaimer {{
            margin-top: 16px;
            padding: 12px;
            background: #fdf6f0;
            border-radius: 10px;
            font-size: 11px;
            color: #9e6555;
            line-height: 1.6;
        }}
        
        /* 分割线 */
        .section-divider {{
            height: 1px;
            background: linear-gradient(to right, transparent, var(--gray-200), transparent);
            margin: 20px 0;
        }}
        
        /* 维度标签 */
        .dim-badge {{
            display: inline-block;
            padding: 3px 10px;
            border-radius: 20px;
            font-size: 11px;
            font-weight: 600;
        }}
        .dim-badge.high {{
            background: var(--charge-light);
            color: #5a7a52;
        }}
        .dim-badge.mid {{
            background: #f5f0e8;
            color: var(--primary);
        }}
        .dim-badge.low {{
            background: #fdf6f0;
            color: #9e6555;
        }}
        
        /* 子能力条 */
        .sub-bar-container {{
            display: flex;
            align-items: center;
            gap: 8px;
            margin: 6px 0;
        }}
        .sub-bar-label {{
            font-size: 11px;
            color: var(--secondary);
            min-width: 60px;
        }}
        .sub-bar-track {{
            flex: 1;
            height: 6px;
            background: var(--gray-200);
            border-radius: 3px;
            overflow: hidden;
        }}
        .sub-bar-fill {{
            height: 100%;
            border-radius: 3px;
        }}
        .sub-bar-score {{
            font-size: 11px;
            font-weight: 700;
            min-width: 28px;
            text-align: right;
        }}
        
        /* 打印优化 */
        @media print {{
            body {{
                background: white;
                padding: 0;
            }}
            .card {{
                box-shadow: none;
                border: 1px solid #e8e0d8;
            }}
        }}
    </style>
</head>
<body>
    <div class="card">
        <div class="result-header">
            <h2>✨ 8维能力测评报告</h2>
            <p>{user_name} | {industry} | {experience}</p>
        </div>
        
        <div id="resultContent"></div>
    </div>
    
    <script>
        {engine_data_js}
    </script>
    
    <script>
        {engine_js}
    </script>
    
    <script>
        // DIM_INFO 定义
        const DIM_INFO = {{
            COG: {{ name: '认知能力', icon: '🧠', color: '#3b82f6' }},
            TEC: {{ name: '技术掌握', icon: '💻', color: '#10b981' }},
            COM: {{ name: '理解表达', icon: '💬', color: '#f59e0b' }},
            SOC: {{ name: '社交技能', icon: '🤝', color: '#ef4444' }},
            ORG: {{ name: '策划执行', icon: '🎯', color: '#8b5cf6' }},
            PRS: {{ name: '解决问题', icon: '⚡', color: '#ec4899' }},
            MGT: {{ name: '管理技能', icon: '👥', color: '#14b8a6' }},
            LLA: {{ name: '持续学习', icon: '📚', color: '#f97316' }}
        }};
        
        // 全局数据
        const scores = {scores_js};
        const answers = {answers_js};
        const userName = '{user_name}';
        const industry = '{industry}';
        const experience = '{experience}';
        
        // 渲染函数
        function getScoreColor(s) {{
            if (s >= 4.0) return '#7d9b76';
            if (s >= 3.0) return '#8b7355';
            return '#c4907a';
        }}
        
        function getLevelBadge(s) {{
            if (s >= 4.0) return {{ label: '高', cls: 'high' }};
            if (s >= 3.0) return {{ label: '中', cls: 'mid' }};
            return {{ label: '低', cls: 'low' }};
        }}
        
        function getSubScores(dim) {{
            const dimQuestions = {{
                COG: [1, 2, 3, 4, 5, 6],
                TEC: [7, 8, 9, 10, 11, 12],
                COM: [13, 14, 15, 16, 17, 18],
                SOC: [19, 20, 21, 22, 23, 24],
                ORG: [25, 26, 27, 28, 29, 30],
                PRS: [31, 32, 33, 34, 35, 36],
                MGT: [37, 38, 39, 40, 41, 42],
                LLA: [43, 44, 45, 46, 47, 48]
            }};
            
            const subNames = {{
                COG: ['信息提炼', '逻辑推理', '快速学习'],
                TEC: ['数字生产力', '技术适应力', '故障排查'],
                COM: ['解码能力', '精炼表达', '口头影响力'],
                SOC: ['情绪觉察', '冲突协调', '关系建立'],
                ORG: ['目标规划', '高标准执行', '资源管理'],
                PRS: ['Plan B产出', '根源分析', '创新方案'],
                MGT: ['任务预期管理', '优先级取舍', '授权追踪'],
                LLA: ['知识更新', '主动探索', '挫折转化']
            }};
            
            const names = subNames[dim] || [];
            return names.map((n, i) => {{
                const qIds = dimQuestions[dim].slice(i * 2, i * 2 + 2);
                const avg = qIds.reduce((s, qId) => s + (answers[qId] || 0), 0) / Math.max(qIds.length, 1);
                return {{ name: n, score: avg }};
            }});
        }}
        
        function renderResult() {{
            const dimOrder = ['COG','TEC','COM','SOC','ORG','PRS','MGT','LLA'];
            const avgAll = dimOrder.reduce((s, d) => s + scores[d].average, 0) / 8;
            const sorted = dimOrder.map(d => ({{ dim: d, score: scores[d].average }})).sort((a,b) => b.score - a.score);
            const top3 = sorted.slice(0, 3);
            const bot3 = sorted.slice(-3).reverse();
            const highest = sorted[0];
            const lowest = sorted[sorted.length-1];
            
            const chargeDims = sorted.filter(d => d.score >= 4.0);
            const shieldDims = sorted.filter(d => d.score < 3.0);
            
            let html = '';
            
            // 第一阶段：画像
            html += `<div class="phase-section">
                <div class="phase-title">第一阶段：【画像】职场禀赋与双向定位</div>
                <div class="phase-subtitle">Phase 1: Professional Profile & Dual Positioning</div>`;
            
            // 情绪天气预报
            const topDim = DIM_INFO[highest.dim];
            const lowDim = DIM_INFO[lowest.dim];
            html += `<div class="weather-card">
                <div class="weather-title">🌤️ 今日心智状态提醒</div>
                <div class="weather-text">
                    您的${{topDim.name}}（${{highest.score.toFixed(1)}}）正在全力运转，请给您的${{lowDim.name}}（${{lowest.score.toFixed(1)}}）留出特别关注，防止其在关键时刻拖累整体表现。
                </div>
            </div>`;
            
            // 高分数据锚点
            if (chargeDims.length > 0) {{
                html += `<table class="anchor-table">
                    <tr style="background:#7d9b76;color:white">
                        <th style="border-radius:8px 0 0 0">数据锚点（≥4.0 高分项）</th>
                        <th style="text-align:left">维度</th>
                        <th style="text-align:center">得分</th>
                        <th style="text-align:center;border-radius:0 8px 0 0">评级</th>
                    </tr>`;
                chargeDims.forEach((item, i) => {{
                    const info = DIM_INFO[item.dim];
                    html += `<tr style="background:#f5f9f4">
                        <td style="text-align:center;color:#7d9b76;font-weight:700">高分${{i+1}}</td>
                        <td>${{info.icon}} ${{info.name}}</td>
                        <td style="text-align:center;font-weight:700">${{item.score.toFixed(1)}}</td>
                        <td style="text-align:center"><span class="dim-badge high">${{getLevelBadge(item.score).label}}</span></td>
                    </tr>`;
                }});
                html += `</table>`;
            }}
            
            html += `</div>`;
            
            // 第二阶段：动态张力
            html += `<div class="section-divider"></div>`;
            html += `<div class="phase-section">
                <div class="phase-title">第二阶段：【动态张力】成就背后的心智成本</div>
                <div class="phase-subtitle">Phase 2: Psychological Cost of Achievement</div>`;
            
            html += `</div>`;
            
            // 第三阶段：场景演练
            html += `<div class="section-divider"></div>`;
            html += `<div class="phase-section">
                <div class="phase-title">第三阶段：【场景演练】求进与避坑的实战模拟</div>
                <div class="phase-subtitle">Phase 3: Scenario Simulation - Advance vs Defend</div>`;
            
            html += `</div>`;
            
            // 第四阶段：进化指南
            html += `<div class="section-divider"></div>`;
            html += `<div class="phase-section">
                <div class="phase-title">第四阶段：【进化指南】优势扩容与安全垫</div>
                <div class="phase-subtitle">Phase 4: Advantage Expansion & Safety Net</div>`;
            
            html += `</div>`;
            
            // 附录：8维分数总览
            html += `<div class="section-divider"></div>`;
            html += `<div class="appendix-section">
                <div class="appendix-title">附录：8维能力分数总览</div>`;
            
            html += `<div class="score-overview">`;
            for (const dim of dimOrder) {{
                const s = scores[dim];
                const info = DIM_INFO[dim];
                const color = getScoreColor(s.average);
                const badge = getLevelBadge(s.average);
                const isHigh = s.average >= 4.0;
                const isLow = s.average < 3.0;
                html += `<div class="score-overview-item ${{isHigh ? 'high-light' : ''}} ${{isLow ? 'low-light' : ''}}">
                    <div class="icon">${{info.icon}}</div>
                    <div class="name">${{info.name}}</div>
                    <div class="score" style="color:${{color}}">${{s.average.toFixed(1)}}</div>
                    <span class="dim-badge ${{badge.cls}}">${{badge.label}}</span>
                </div>`;
            }}
            html += `</div>`;
            
            // 评分说明
            html += `<div class="legend-card">
                <div class="legend-title">评分说明</div>
                <div class="legend-item">• 5分：杰出 — 该能力已形成明显优势，可作为职业发展的核心武器。</div>
                <div class="legend-item">• 4-4.9分：高 — 该能力高于平均水平，具备竞争力，可重点发挥。</div>
                <div class="legend-item">• 3-3.9分：中 — 该能力处于正常范围，有提升空间，建议针对性练习。</div>
                <div class="legend-item">• 2-2.9分：低（防护区）— 该能力低于平均水平，需要通过策略或代偿方式规避风险。</div>
            </div>`;
            
            html += `</div>`;
            
            // 页脚
            html += `<div class="result-footer">© 2026 Santa Chow 香港求职咨询 | 8维能力测评报告</div>`;
            
            // 免责声明
            html += `<div class="disclaimer">
                <b>免责声明：</b>本报告基于自评数据，仅供参考。自评可能受个人认知偏差影响，建议结合他人的客观反馈进行综合分析。如需一对一专业求职定位咨询，请联系 Santa Chow 教练获取个人化指导。
            </div>`;
            
            document.getElementById('resultContent').innerHTML = html;
        }}
        
        // 页面加载完成后渲染
        document.addEventListener('DOMContentLoaded', function() {{
            renderResult();
        }});
    </script>
</body>
</html>'''
    
    return html
