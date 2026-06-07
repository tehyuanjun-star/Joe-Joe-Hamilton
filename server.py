#!/usr/bin/env python3
"""
server.py — 交易日记本地服务器
替代 Python http.server，提供 trades.json 读写 API

启动方法：
    python server.py
    然后浏览器打开 http://localhost:3737
"""
import json
import os
import sys
from flask import Flask, jsonify, request, send_from_directory, abort

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, 'trades.json')
HTML_FILE = '交易日记.html'
PORT = 3737

app = Flask(__name__)

# ─── 数据读写 ───────────────────────────────────────────────────────
def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        # 兼容旧格式（裸数组）
        if isinstance(data, list):
            return {'trades': data, 'tactics': []}
        return data
    return {'trades': [], 'tactics': []}


def save_data(data: dict):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ─── 路由 ──────────────────────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory(BASE_DIR, HTML_FILE)


@app.route('/api/trades', methods=['GET'])
def api_get_trades():
    return jsonify(load_data())


@app.route('/api/trades', methods=['POST'])
def api_post_trades():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or 'trades' not in data:
        abort(400, 'Expected JSON body: {"trades": [...], "tactics": [...]}')
    save_data(data)
    return jsonify({'ok': True, 'count': len(data['trades'])})


@app.route('/api/status', methods=['GET'])
def api_status():
    """供网页查询最后一次 IBKR 同步状态"""
    proc_file = os.path.join(BASE_DIR, 'ibkr_processed.json')
    data = load_data()
    last_updated = None
    if data['trades']:
        last_updated = data['trades'][0].get('updatedAt')
    processed = 0
    if os.path.exists(proc_file):
        with open(proc_file) as f:
            processed = len(json.load(f))
    return jsonify({
        'total_trades': len(data['trades']),
        'last_updated': last_updated,
        'processed_executions': processed,
    })


# ─── 入口 ──────────────────────────────────────────────────────────
if __name__ == '__main__':
    print('=' * 50)
    print('  交易日记服务器')
    print(f'  地址：http://localhost:{PORT}')
    print(f'  数据：{DATA_FILE}')
    print('  按 Ctrl+C 停止')
    print('=' * 50)
    app.run(host='127.0.0.1', port=PORT, debug=False, use_reloader=False)
