"""
生成 V3 模拟报告测试脚本（基于V2修复所有bug）
"""
import sys
sys.path.insert(0, r'C:\Users\85255\Downloads')

# 模拟陈志明的8维分数
mock_scores = {
    'COG': {'name': '认知能力',   'average': 4.5, 'level': '优秀'},
    'TEC': {'name': '技术掌握',   'average': 4.2, 'level': '优秀'},
    'COM': {'name': '理解表达',   'average': 4.0, 'level': '优秀'},
    'SOC': {'name': '社交技能',   'average': 3.8, 'level': '良好'},
    'ORG': {'name': '策划执行',   'average': 3.0, 'level': '中等'},
    'PRS': {'name': '解决问题',   'average': 2.8, 'level': '待提升'},
    'MGT': {'name': '管理技能',   'average': 2.5, 'level': '待提升'},
    'LLA': {'name': '持续学习',   'average': 4.3, 'level': '优秀'},
}

# 注册字体
from quiz_api_server import register_fonts
font_name = register_fonts()
print(f"字体: {font_name}")

# 生成 V3 报告（使用V2函数，bug已修复）
from quiz_api_server import generate_pdf_48_v2

buffer = generate_pdf_48_v2(
    result_id=999,
    scores=mock_scores,
    user_name='陈志明',
    experience='3-5年'
)

output_path = r'C:\Users\85255\Downloads\mock_report_V3.pdf'
with open(output_path, 'wb') as f:
    f.write(buffer.getvalue())

print(f"V3 报告已生成: {output_path}")
print(f"文件大小: {len(buffer.getvalue())} bytes")
