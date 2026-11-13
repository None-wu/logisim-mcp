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
const os = require('node:os');
const fs = require('node:fs');

const PKG_ROOT = path.resolve(__dirname, '..');
const SERVER_ENTRY = path.join(PKG_ROOT, 'server.py');
const SERVER_KEY = 'logisim';
const GITHUB_SPEC = 'github:None-wu/logisim-mcp';
const MIN_MINOR = 9; // Python 3.9+

/* ---------------- 一键安装：--install / --uninstall / --project ---------------- */

function parseFlags(argv) {
  const flags = {};
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === '--install' || a === '--uninstall' || a === '--project') {
      flags[a.slice(2)] = true;
    } else if (a === '--dry-run') {
      flags.dryRun = true;
    } else if (a === '--client') {
      flags.client = argv[(i += 1)];
    }
  }
  return flags;
}

function clientTargets() {
  const home = os.homedir();
  const appData = process.env.APPDATA || path.join(home, 'AppData', 'Roaming');
  return [
    // create: 配置文件不存在时，只要客户端自己的目录存在就允许新建。
    // claude-code 的 ~/.claude.json 是状态文件，绝不能替它凭空创建。
    { id: 'workbuddy', name: 'WorkBuddy', create: true, files: [path.join(home, '.workbuddy', 'mcp.json')] },
    {
      id: 'claude-desktop',
      name: 'Claude Desktop',
      create: true,
      files: [
        path.join(appData, 'Claude', 'claude_desktop_config.json'),
        path.join(home, 'Library', 'Application Support', 'Claude', 'claude_desktop_config.json'),
      ],
    },
    { id: 'claude-code', name: 'Claude Code', create: false, files: [path.join(home, '.claude.json')] },
    { id: 'cursor', name: 'Cursor', create: true, files: [path.join(home, '.cursor', 'mcp.json')] },
    { id: 'windsurf', name: 'Windsurf', create: true, files: [path.join(home, '.codeium', 'windsurf', 'mcp_config.json')] },
    {
      id: 'cline',
      name: 'Cline (VS Code)',
      create: true,
      files: [
        path.join(appData, 'Code', 'User', 'globalStorage', 'saoudrizwan.claude-dev', 'settings', 'cline_mcp_settings.json'),
        path.join(home, '.config', 'Code', 'User', 'globalStorage', 'saoudrizwan.claude-dev', 'settings', 'cline_mcp_settings.json'),
      ],
    },
    {
      id: 'vscode',
      name: 'VS Code (Copilot MCP)',
      create: true,
      files: [
        path.join(appData, 'Code', 'User', 'mcp.json'),
        path.join(home, '.config', 'Code', 'User', 'mcp.json'),
      ],
    },
  ];
}

/** 宽松解析 JSONC（容忍 // 注释与尾逗号），失败则当作空对象。 */
function loadJsonLoose(file) {
  let text = '';
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch {
    return {};
  }
  const stripped = text
    .replace(/^\uFEFF/, '')
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1')
    .replace(/,(\s*[}\]])/g, '$1');
  try {
    const parsed = JSON.parse(stripped);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return null; // 明确损坏，宁可不动
  }
}

/** 安装条目：全局/仓库安装用绝对路径（启动快、离线可用），否则退回 npx 拉取。 */
function serverEntry() {
  const self = path.resolve(process.argv[1] || __filename);
  const stable =
    self.toLowerCase().endsWith('logisim-mcp.js') &&
    !self.toLowerCase().includes('_npx') &&
    fs.existsSync(self);
  if (stable) return { command: process.execPath, args: [self] };
  return { command: 'npx', args: ['-y', GITHUB_SPEC] };
}

function mutateConfig(file, mode) {
  const cfg = loadJsonLoose(file);
  if (cfg === null) return { changed: false, note: 'JSON 解析失败，已跳过（避免写坏配置）', cfg: null };
  if (!cfg.mcpServers || typeof cfg.mcpServers !== 'object') cfg.mcpServers = {};
  const existed = Object.prototype.hasOwnProperty.call(cfg.mcpServers, SERVER_KEY);
  if (mode === 'remove') {
    if (!existed) return { changed: false, note: '本来就没有条目', cfg };
    delete cfg.mcpServers[SERVER_KEY];
    return { changed: true, note: '已移除', cfg };
  }
  cfg.mcpServers[SERVER_KEY] = serverEntry();
  return { changed: true, note: existed ? '已更新（覆盖旧条目）' : '新增', cfg };
}

function runInstaller(flags) {
  const mode = flags.uninstall ? 'remove' : 'upsert';
  const targets = [];
  if (flags.project) {
    targets.push({ id: 'project', name: `项目级 .mcp.json（${process.cwd()}）`, create: true, files: [path.join(process.cwd(), '.mcp.json')] });
  } else {
    for (const t of clientTargets()) {
      if (!flags.client || t.id === flags.client) targets.push(t);
    }
  }

  const entry = serverEntry();
  let touched = 0;
  for (const target of targets) {
    const exists = (f) => fs.existsSync(f);
    const file =
      target.files.find(exists) ||
      (target.create ? target.files.find((f) => exists(path.dirname(f)) && path.dirname(f) !== os.homedir()) : undefined);
    if (!file) {
      console.log(`[ ] ${target.name}: 未检测到安装目录，跳过`);
      continue;
    }
    const found = exists(file);
    const outcome = mutateConfig(file, mode);
    if (!outcome.changed) {
      console.log(`[=] ${target.name}: ${file} — ${outcome.note}`);
      continue;
    }
    if (flags.dryRun) {
      console.log(`[*] ${target.name}: ${file} — (dry-run) 将${mode === 'remove' ? '移除' : '写入'} ${SERVER_KEY}: ${JSON.stringify(entry)}`);
    } else {
      if (found) {
        const backup = `${file}.bak-logisim`;
        fs.copyFileSync(file, backup);
        console.log(`    备份: ${backup}`);
      } else {
        fs.mkdirSync(path.dirname(file), { recursive: true });
      }
      fs.writeFileSync(file, JSON.stringify(outcome.cfg, null, 2) + '\n', 'utf8');
      touched += 1;
      console.log(`[+] ${target.name}: ${file} — ${outcome.note}`);
    }
  }

  console.log(
    `\n${mode === 'remove' ? '卸载' : '安装'}完成（${flags.dryRun ? 'dry-run，未实际写入' : `${touched} 个文件已修改`}）。` +
      `\n启动条目: ${JSON.stringify(entry)}`
  );
  if (mode !== 'remove') {
    console.log(
      '\n提醒：\n' +
        '  · 需要 Python ≥ 3.9（无需 pip）与 Logisim-evolution；用 --doctor 验证\n' +
        '  · WorkBuddy 用户：首次使用请到「连接器管理 → 自定义连接器」里对新出现的 logisim 点「信任」'
    );
    if (entry.command === 'npx') {
      console.log('  · 当前条目走 npx 拉取，建议先 `npm i -g ' + GITHUB_SPEC + '` 再重跑 --install，可换成稳定的绝对路径');
    }
  }
  process.exit(0);
}

const FLAGS = parseFlags(process.argv.slice(2));
if (FLAGS.install || FLAGS.uninstall || FLAGS.project) runInstaller(FLAGS);

/* ---------------- 默认路径：拉起 Python MCP 服务器 ---------------- */


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
