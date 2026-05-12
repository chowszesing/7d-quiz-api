"""
最小化Flask应用 - 用于测试PDF端点是否正常工作
"""
from flask import Flask, jsonify, send_file
from contextlib import contextmanager
import sqlite3
import os
from datetime import datetime
import json

app = Flask(__name__)

DATABASE = os.environ.get('DATABASE', 'quiz_results.db')

@contextmanager
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

@app.route('/api/health')
def health():
    return jsonify({'status': 'ok', 'version': 'minimal_app', 'timestamp': datetime.now().isoformat()})

@app.route('/api/quiz/report_full/<int:result_id>')
def report_full(result_id):
    """测试端点 - 不生成PDF，只返回调试信息"""
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute('SELECT id, user_name FROM quiz_results_48 WHERE id = ?', (result_id,))
            row = c.fetchone()
        
        if not row:
            return jsonify({'error': 'Not found', 'result_id': result_id}), 404
        
        # 不生成PDF，只返回调试信息
        return jsonify({
            'status': 'success',
            'message': 'report_full endpoint is working!',
            'result_id': result_id,
            'user_name': row['user_name'],
            'note': 'This is a test endpoint. PDF generation is disabled in minimal_app.'
        })
    except Exception as e:
        return jsonify({'error': str(e), 'result_id': result_id}), 500

@app.route('/api/quiz/report_48/<int:result_id>')
def report_48(result_id):
    """测试端点 - 跟report_full一样，用于对比"""
    return jsonify({
        'status': 'success',
        'message': 'report_48 endpoint is working!',
        'result_id': result_id,
        'endpoint': 'report_48'
    })

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
