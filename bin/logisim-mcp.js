#!/usr/bin/env node
'use strict';

/**
 * logisim-mcp — npm 启动器
 *
 * npm 包本身只是分发壳：真正的 MCP 服务器是同一目录下的 Python 实现
 * （logisim_mcp/ + server.py，纯标准库，无需 pip 安装）。本脚本负责：
 *   1. 找到可用的 Python 3.9+（LOGISIM_MCP_PYTHON > py -3 > python3 > python）
 *   2. 以 stdio 直通方式启动 server.py，让 JSON-RPC 双向流动
 *   3. 转发退出码与中断信号
 */

const { spawn, spawnSync } = require('node:child_process');
const path = require('node:path');

const PKG_ROOT = path.resolve(__dirname, '..');
const SERVER_ENTRY = path.join(PKG_ROOT, 'server.py');
const MIN_MINOR = 9; // Python 3.9+

function probe(cmd, prefix) {
  const res = spawnSync(
    cmd,
    [...prefix, '-c', 'import sys; sys.stdout.write("%d.%d" % sys.version_info[:2])'],
    { encoding: 'utf8', windowsHide: true, timeout: 15000 }
  );
  if (res.error || res.status !== 0) return null;
  const matched = String(res.stdout || '').trim().match(/^(\d+)\.(\d+)$/);
  if (!matched) return null;
  const major = Number(matched[1]);
  const minor = Number(matched[2]);
  return { cmd, prefix, version: `${major}.${minor}`, ok: major === 3 && minor >= MIN_MINOR };
}

function findPython() {
  const tried = [];
  const candidates = [];
  if (process.env.LOGISIM_MCP_PYTHON) candidates.push([process.env.LOGISIM_MCP_PYTHON, []]);
  if (process.platform === 'win32') {
    candidates.push(['py', ['-3']], ['python', []], ['python3', []]);
  } else {
    candidates.push(['python3', []], ['python', []]);
  }

  let tooOld = null;
  for (const [cmd, prefix] of candidates) {
    const found = probe(cmd, prefix);
    if (!found) {
      tried.push([cmd, ...prefix].join(' '));
      continue;
    }
    if (found.ok) return found;
    tooOld = tooOld || found;
  }
  const detail = tooOld
    ? `找到 Python ${tooOld.version}（${[tooOld.cmd, ...tooOld.prefix].join(' ')}），但需要 3.9 及以上`
    : `尝试过：${tried.join(', ')}`;
  return { error: detail };
}

function fail(message) {
  process.stderr.write(`[logisim-mcp] ${message}\n`);
  process.exit(1);
}

const python = findPython();
if (!python || python.error) {
  fail(
    `未找到可用的 Python 3.9+：${python ? python.error : 'unknown'}。\n` +
      '  · 安装 Python: https://www.python.org/downloads/\n' +
      '  · 或指定解释器: LOGISIM_MCP_PYTHON=/path/to/python npx logisim-mcp'
  );
}

if (!require('node:fs').existsSync(SERVER_ENTRY)) {
  fail(`安装不完整，缺少 ${SERVER_ENTRY}。请重新安装本包。`);
}

const child = spawn(python.cmd, [...python.prefix, SERVER_ENTRY, ...process.argv.slice(2)], {
  cwd: process.cwd(), // 相对路径参数以调用者目录为基准
  stdio: 'inherit',
  windowsHide: true,
  env: {
    ...process.env,
    PYTHONPATH: [PKG_ROOT, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
    PYTHONIOENCODING: 'utf-8',
    PYTHONUTF8: '1',
  },
});

child.on('error', (err) => {
  fail(`无法启动 Python（${python.cmd}）：${err.message}`);
});

for (const sig of ['SIGINT', 'SIGTERM', 'SIGHUP']) {
  try {
    process.on(sig, () => {
      if (!child.killed) child.kill(sig);
    });
  } catch {
    /* Windows 上部分信号不可用，忽略 */
  }
}

child.on('exit', (code, signal) => {
  if (signal) process.exit(0);
  process.exit(code === null ? 1 : code);
});
