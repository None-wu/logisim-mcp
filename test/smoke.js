#!/usr/bin/env node
'use strict';

/**
 * 冒烟测试：不依赖任何测试框架，验证 npm 包装层能真正把 Python MCP 服务器跑起来。
 *   1. `logisim-mcp --version`
 *   2. stdio JSON-RPC：initialize → tools/list（应返回 9 个工具）
 *   3. `logisim-mcp --doctor` 能输出 JSON 自检信息
 */

const { spawn, spawnSync } = require('node:child_process');
const path = require('node:path');

const BIN = path.resolve(__dirname, '..', 'bin', 'logisim-mcp.js');
const EXPECTED_TOOLS = 9;

let failures = 0;
function check(name, ok, detail) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? ' — ' + detail : ''}`);
  if (!ok) failures += 1;
}

function rpc(requests, timeoutMs = 40000) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [BIN], { stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
    const pending = new Set(requests.map((r) => r.id));
    const replies = new Map();
    let stdout = '';
    let stderr = '';
    let settled = false;

    const timer = setTimeout(() => finish(new Error(`timeout after ${timeoutMs}ms; stderr=${stderr.slice(-500)}`)), timeoutMs);

    function finish(err) {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try { child.kill(); } catch { /* ignore */ }
      if (err) reject(err);
      else resolve(replies);
    }

    child.stdout.setEncoding('utf8');
    child.stdout.on('data', (chunk) => {
      stdout += chunk;
      let idx;
      while ((idx = stdout.indexOf('\n')) >= 0) {
        const line = stdout.slice(0, idx).trim();
        stdout = stdout.slice(idx + 1);
        if (!line) continue;
        let msg;
        try { msg = JSON.parse(line); } catch { continue; }
        if (msg && msg.id !== undefined && pending.has(msg.id)) {
          pending.delete(msg.id);
          replies.set(msg.id, msg);
        }
      }
      if (pending.size === 0) finish(null);
    });
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', (c) => { stderr += c; });
    child.on('error', (err) => finish(err));

    for (const req of requests) child.stdin.write(JSON.stringify(req) + '\n');
  });
}

(async () => {
  const version = spawnSync(process.execPath, [BIN, '--version'], { encoding: 'utf8', timeout: 40000 });
  check(
    '--version 可执行',
    version.status === 0 && /logisim-evolution-mcp/.test(version.stdout || ''),
    (version.stdout || version.stderr || '').trim().split('\n')[0]
  );

  try {
    const replies = await rpc([
      {
        jsonrpc: '2.0',
        id: 1,
        method: 'initialize',
        params: {
          protocolVersion: '2025-06-18',
          capabilities: {},
          clientInfo: { name: 'npm-smoke', version: '1.0.0' },
        },
      },
      { jsonrpc: '2.0', id: 2, method: 'tools/list', params: {} },
    ]);

    const init = replies.get(1);
    const serverInfo = init && init.result && init.result.serverInfo;
    check(
      'initialize 握手',
      Boolean(serverInfo && serverInfo.name === 'logisim-evolution-mcp'),
      serverInfo ? `${serverInfo.name} ${serverInfo.version}` : JSON.stringify(init)
    );

    const list = replies.get(2);
    const tools = (list && list.result && list.result.tools) || [];
    check('tools/list 返回工具', tools.length === EXPECTED_TOOLS, `${tools.length}/${EXPECTED_TOOLS}: ${tools.map((t) => t.name).join(', ')}`);
  } catch (err) {
    check('stdio JSON-RPC 往返', false, err.message);
  }

  const doctor = spawnSync(process.execPath, [BIN, '--doctor'], { encoding: 'utf8', timeout: 60000 });
  let doctorJson = null;
  try {
    doctorJson = JSON.parse((doctor.stdout || '').split('\ntools (')[0]);
  } catch { /* not fatal */ }
  check(
    '--doctor 输出自检 JSON',
    Boolean(doctorJson && 'found' in doctorJson),
    doctorJson ? `found=${doctorJson.found}` : (doctor.stderr || '').slice(0, 200)
  );

  const dry = spawnSync(process.execPath, [BIN, '--install', '--dry-run'], { encoding: 'utf8', timeout: 30000 });
  check(
    '--install --dry-run 预演不落盘',
    dry.status === 0 && /dry-run，未实际写入/.test(dry.stdout || ''),
    (dry.stdout || '').split('\n').find((l) => l.startsWith('[')) || ''
  );

  console.log(failures === 0 ? '\nSMOKE OK' : `\nSMOKE FAILED (${failures})`);
  process.exit(failures === 0 ? 0 : 1);
})();
