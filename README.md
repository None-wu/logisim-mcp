# Logisim-evolution MCP Server

把 [Logisim-evolution](https://github.com/logisim-evolution/logisim-evolution)（数字逻辑设计与仿真软件，当前最新版 **v5.0.0**）接入 **MCP（Model Context Protocol）**，让 Claude Desktop、Cursor、Cline、Continue、WorkBuddy/CodeBuddy 等主流 Agent 能直接：

- **读懂电路**：解析 `.circ` 工程，列出电路/引脚/元件/连线网络；
- **真正仿真**：调用官方无头 `--test-vector` 引擎求输出，语义与 GUI 完全一致；
- **推导逻辑**：枚举真值表并做 Quine–McCluskey 最简化，给出与或表达式；
- **设计电路**：从 JSON netlist 自动布局布线生成合法 `.circ`，并回读校验；
- **检查**：结构 lint（悬空线/短路/缺驱动等）；
- **GUI 同级体验**：生成的文件带完整工程骨架（13 个内置元件库 + 默认工具条），打开即与手绘工程一致。

**零第三方依赖**：只要 Python 3.9+（标准库即可），不需要 pip install。

---

## 安装

### npm / npx（推荐，跨平台）

```bash
npm i -g github:None-wu/logisim-mcp        # 全局安装 → 得到 logisim-mcp 命令
npx -y github:None-wu/logisim-mcp --doctor # 免安装直接跑一次
```

在本仓库目录里也可以 `npm i -g .` 或 `npx . --doctor`。
（若日后发布到 npm registry，则简化为 `npm i -g logisim-mcp` / `npx logisim-mcp`。）

npm 包是**分发壳**：真正的服务器是包内的 Python 实现（纯标准库、零依赖）。
启动时会依次尝试 `LOGISIM_MCP_PYTHON` → `py -3` → `python3` → `python`，
并校验 Python ≥ 3.9；相对路径参数以**调用者的当前工作目录**为基准。
需要 Node ≥ 16.7 与 Python ≥ 3.9（不需要 pip install）。

### 一键安装到所有 MCP 客户端（`--install`）

```bash
npx -y github:None-wu/logisim-mcp --install     # 探测本机已装的客户端并写入配置
# 全局安装后也可: logisim-mcp --install
```

自动探测：**WorkBuddy、Claude Desktop、Claude Code、Cursor、Windsurf、Cline、
VS Code（Copilot MCP）**。行为约定：

- 只动客户端自己的目录（Claude Code 的状态文件 `~/.claude.json` 仅在已存在时更新，绝不凭空创建）；
- 写入前自动备份为 `<配置文件>.bak-logisim`，其他已有 MCP 服务器条目原样保留；
- `--client <id>` 只写指定客户端（id：`workbuddy` / `claude-desktop` / `claude-code` / `cursor` / `windsurf` / `cline` / `vscode`）；
- `--dry-run` 预演不落盘；`--uninstall` 移除条目；`--project` 写当前目录的 `.mcp.json`（Claude Code / Cursor 支持项目级）；
- WorkBuddy 用户安装后需到「连接器管理 → 自定义连接器」对 `logisim` 点一次「信任」。

也可以把这句话直接发给任何 agent，让它替你完成：

> 帮我安装 logisim-mcp：先运行 `npx -y github:None-wu/logisim-mcp --install`，
> 再运行 `npx -y github:None-wu/logisim-mcp --doctor` 验证环境（找到 Logisim 即成功），
> 把结果和需要手动做的步骤告诉我。

### 从源码（无 Node 环境）

```bash
git clone https://github.com/None-wu/logisim-mcp && cd logisim-mcp
python server.py --doctor
```

---

## 快速开始

```bash
# 自检：确认 Python 版本与 Logisim 可执行文件
npx -y github:None-wu/logisim-mcp --doctor   # 或 python server.py --doctor

# stdio 模式（默认，供 MCP 客户端拉起）
npx -y github:None-wu/logisim-mcp

# HTTP 模式（可选）
npx -y github:None-wu/logisim-mcp --http 8765   # 端点 http://127.0.0.1:8765/mcp

# 回归自测（41 项 Python 断言 + 5 项 npm 包装冒烟测试）
python tests/selftest.py
npm test
```

### 环境要求

| 项目 | 说明 |
|---|---|
| Node.js | ≥ 16.7（仅 npm 包装层需要；直接用 `python server.py` 可跳过） |
| Python | ≥ 3.9，无任何第三方包 |
| Logisim-evolution | 官方发行版（exe / jar 均可），**v5.0.0 已验证** |
| 定位方式 | 依次搜索：`LOGISIM_EXE` → `~/.logisim-mcp.json`（`{"exe": "C:/path/logisim-evolution.exe"}`）→ 包目录及其上级 → **当前工作目录及上两级（限深 3 层）** → `Program Files` / `/Applications` / `/usr/local/bin` → `PATH` |

---

## 客户端接入配置

### 方式一：npx（推荐，无需写绝对路径）

任何 stdio 客户端都可以直接用：

```json
{
  "mcpServers": {
    "logisim": {
      "command": "npx",
      "args": ["-y", "github:None-wu/logisim-mcp"],
      "timeout": 120000
    }
  }
}
```

> Windows 上若 `npx` 不在 PATH，可写 `"command": "npx.cmd"`。

### 方式二：Python 直调

以下路径按你的实际安装位置修改（本机示例根目录为
`E:/数字逻辑/logisim-evolution-5.0.0-windows-amd64/logisim-mcp`）。

### Claude Desktop（`claude_desktop_config.json`）

```json
{
  "mcpServers": {
    "logisim": {
      "command": "python",
      "args": ["E:/数字逻辑/logisim-evolution-5.0.0-windows-amd64/logisim-mcp/server.py"]
    }
  }
}
```

### Cursor（`.cursor/mcp.json`）

```json
{
  "mcpServers": {
    "logisim": {
      "command": "python",
      "args": ["E:/数字逻辑/logisim-evolution-5.0.0-windows-amd64/logisim-mcp/server.py"]
    }
  }
}
```

### OpenCode（`opencode.json`，全局 `~/.config/opencode/opencode.json` 或项目根目录）

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "logisim": {
      "type": "local",
      "command": ["python", "E:/数字逻辑/logisim-evolution-5.0.0-windows-amd64/logisim-mcp/server.py"],
      "enabled": true,
      "timeout": 120000
    }
  }
}
```

也可用命令行添加：`opencode mcp add logisim -- python <server.py 绝对路径>`。`timeout` 建议调大（真值表枚举 10+ 输入时仿真耗时可能超过默认 30s）。

### Cline / Continue / 其他 stdio 客户端

同样填写 `command: python`、`args: [server.py 绝对路径]`。WorkBuddy / CodeBuddy 用户把同样内容写进 `~/.workbuddy/mcp.json` 的 `mcpServers` 即可。

### HTTP 接入

先运行 `python server.py --http 8765`，然后在支持 Streamable HTTP 的客户端里填 `http://127.0.0.1:8765/mcp`。

### 无头仿真已知限制（v5.0.0 实测）与内置兼容层

- **Junction** 元件会让无头引擎卡死（GUI 正常），生成器不产出 Junction。
- **Splitter** 元件在无头引擎（`--test-vector` 与 `-tty table` 两条路径）中均不导通（GUI 完全正常）。
  为此服务端内置**等价兼容层**（`splitter_compat.py`）：检测到电路含 Splitter 时，自动生成一个去掉
  Splitter 的等价临时副本——合并方向替换为 N 位总线引脚、分裂方向替换为逐位 1 位引脚，仿真时按
  位映射自动合成/拆分取值，验证完成后输出 `note` 说明。**用户文件保持原样**，GUI 中 Splitter 正常工作。
- Splitter 端口几何严格按官方 `SplitterParameters` 源码建模：合并端 end0 在
  `(loc.x±20, loc.y-gap*⌊fanout/2⌋)`，分支端 end_i 在 end0 正下方 i×gap，默认 bit_{i-1}↔end_i（bit0 在最上方），
  并显式写出 `bit0..bitN` 属性。

### 工程骨架：为什么生成的文件在 GUI 里"功能齐全"

Logisim **不会**在加载时补全工程骨架。`.circ` 里的这几个段落直接决定 GUI 的样子：

| 段落 | 缺失时的界面表现 |
|---|---|
| `<lib desc="..."/>` 列表 | 左侧元件库面板只剩文件里声明的那几个库（常见症状：只剩 Wiring/Gates/I/O/Plexers，找不到 Arithmetic、Memory、TTL、Soc…） |
| `<toolbar>` 工具条 | 窗口顶部快捷工具条**空白**，没有 Poke / Edit / Wiring / Text / Pin / 门电路 / 触发器 |
| `<mappings>` | 右键菜单类快捷操作不可用 |
| `<options>` / `<circuit><a ...>` | 视图状态（网格、缩放、仿真开关）回落为全局默认 |

因此 `circuit_create` 会写出**与 5.0.0 自己"新建工程 → 保存"完全一致**的骨架：13 个内置库、
15 个默认工具条按钮、鼠标绑定、选项，且库名用 5.0 的十进制编号（`0..12`，不是 4.x 的
十六进制 `A/B/C/D`）。校验方式是拿官方 `-n`（加载后另存）过一遍，库表与工具条原样保留即视为通过。

已有文件可用 `circuit_retrofit` 补齐（按 `<lib desc=...>` 重映射库索引，电路内容一字不改）：

```bash
python -m logisim_mcp retrofit "E:/数字逻辑/实验四/voter3.circ" --dry-run   # 预演
python -m logisim_mcp retrofit old.circ                                    # 就地升级
```

---

## 工具清单（9 个）

| 工具 | 作用 |
|---|---|
| `logisim_info` | 环境自检：找到的可执行文件、版本、引擎状态 |
| `circuit_list` | 列出工程里所有电路及输入/输出引脚 |
| `circuit_inspect` | 电路结构详解：元件清单、属性、端口坐标、连线网络 |
| `circuit_simulate` | 无头仿真。组合逻辑传 `inputs`；时序电路传 `steps`（自动复位并逐拍推进） |
| `circuit_truth_table` | 枚举全部输入组合 → 真值表 + QM 最简与或式（仅限组合电路） |
| `circuit_test` | 运行测试向量文件/文本，逐行给出期望 vs 实际 |
| `circuit_create` | JSON netlist → `.circ`（自动美观布局、电气安全布线、加载校验、lint）。支持 `multiplexer`（Plexers，`select` 指定地址位数）与 `splitter` |
| `circuit_lint` | 结构检查：重复标签、悬空导线、无驱动/多驱动、未连接门输入等 |
| `circuit_retrofit` | 把任意 `.circ` 升级为 5.0 原生工程骨架（全量库表 + 默认工具条 + 鼠标绑定 + 选项），库索引按 `desc` 重映射，电路内容不变；支持 `write=false` 预演 |

### 典型 Agent 工作流

```
logisim_info → circuit_list → circuit_inspect → circuit_simulate / circuit_truth_table
（设计新电路）circuit_create → circuit_test → circuit_lint
```

### netlist 示例（`examples/full_adder.netlist.json`）

```json
{
  "circuit": "full_adder",
  "components": [
    {"id": "a",   "type": "input",  "label": "a"},
    {"id": "b",   "type": "input",  "label": "b"},
    {"id": "cin", "type": "input",  "label": "cin"},
    {"id": "x1",  "type": "xor",    "inputs": 2},
    {"id": "a1",  "type": "and",    "inputs": 2},
    {"id": "sum", "type": "output", "label": "sum"}
  ],
  "connections": [
    ["a", "x1.in0"], ["b", "x1.in1"], ["x1.out", "sum"]
  ]
}
```

- 元件类型：`input` `output` `and` `or` `not` `nand` `nor` `xor` `xnor` `buffer` `clock` `led` `button` `constant` `multiplexer` `splitter`
- 端口写法：`id.out` / `id.in0` / `id.in1`… / `id.select`（MUX 地址端）
- 可选 `at: [x, y]` 指定坐标，`width` 指定位宽（总线）
- 生成后自动做：XML 回读 + Logisim 加载冒烟测试 + lint

### 美观布局与布线（circuit_create）

- **布局**：按最长路径分层定列 → 列内重心排序减少交叉 → Gauss–Seidel 对齐松弛
  （让驱动源与负载端口 y 互相靠拢，MUX/分线器自动垂直居中于自己的数据源）→
  按元件类型的最小间距推开 → 列整体居中。效果是常量簇拥在 MUX 旁、开关对齐
  分线器，而不是一字排开拉大扇。
- **布线**：直线优先 → 多汇点走"干线"（一条竖线 + 短水平支线）→ 单汇点贴汇点
  L 形 → 高速路绕行（垂直逃逸 x 可左右偏移扫描，能从被端口围死的"口袋"里逃出）
  → 全程执行不穿端口/不共线重叠/不 T 接他网的可行性检查 → 兜底线只作最后手段。
  重试保留失败最少的尝试，避免"按下葫芦浮起瓢"。

---

## 实现要点（为什么这样做）

1. **仿真走官方引擎**：所有求值都通过 `logisim-evolution --test-vector <circuit> <vec> <file>` 完成，
   子电路、总线、算术元件、时序元件全部由 Logisim 自己解释，MCP 端不做二次建模。
2. **读实际输出的技巧**：向量期望值全填 0，则输出为 0 的行静默通过、非 0 的行会把实际值打印出来，
   一次进程即可拿到整张真值表。
3. **端口几何是实测的**：Logisim 只在"端口坐标重合"时连线。各门默认轴长（AND/OR=50，
   NAND/NOR/XOR=60，XNOR=70，NOT=30，Buffer=20）均通过无头探针逐一实测校准。
4. **布线防短路**：布线器全程执行"不穿他网端口 / 不与他网共线重叠 / 不 T 接他网"
   的可行性检查；直线 → 干线扇出 → L 形 → 高速路绕行逐级降级，实在走不通才兜底。
   合并共线线段时会自动在丁字交点处切开长边——否则"端点落在线内部"的 T 接会
   退化成不导通的十字交叉（Logisim 的连通规则）。
5. **工程头必须是原生骨架**：`<lib>` 决定 GUI 元件库面板、`<toolbar>` 决定顶部工具条，
   Logisim 加载时不会补全。生成器固定写 13 库 + 15 工具按钮 + 鼠标绑定 + 选项，
   并用官方 `-n`（加载后另存）做往返校验：库表与工具条不缩水才算通过。
6. **已知坑（v5.0.0）**：
   - 无头模式遇到 `Junction` 元件会**卡死**（已实测），因此生成器不产出 Junction，
     T 型连接本身会自动导通；
   - 无头模式 **Splitter 不导通**（两条无头路径均实测；GUI 正常）——`simulate` /
     `truth_table` / `test` 会自动走等价兼容副本完成验证，交付文件不受影响；
   - Splitter 各端口都在同一条竖线上，布线器禁止在该竖线上拉垂直走线（否则合并端
     与分支端短路）；
   - 真值表默认上限 1024 行（10 个 1-bit 输入），更多输入请用 `circuit_simulate`。

## 目录结构

```
logisim-mcp/
├─ server.py               # 入口（stdio / --http / --doctor / --version）
├─ pyproject.toml          # 可选：pip/uvx 安装为 logisim-mcp 命令
├─ logisim_mcp/
│  ├─ mcp.py               # MCP 协议层（握手、tools/list、tools/call）
│  ├─ tools_impl.py        # 工具实现
│  ├─ circ.py              # .circ XML 解析
│  ├─ geometry.py          # 元件端口几何（实测校准）
│  ├─ nets.py              # 连线网络并查集
│  ├─ runner.py            # 无头引擎调用与输出解析
│  ├─ truth.py             # 真值表 + Quine–McCluskey
│  ├─ builder.py           # netlist → .circ（布局 + 布线）
│  ├─ template.py          # 5.0 原生工程骨架（库表 / 工具条 / 鼠标绑定 / 选项）
│  ├─ upgrade.py           # 老文件 → 原生骨架（库索引按 desc 重映射）
│  └─ lint.py              # 结构检查
├─ examples/               # 半加器 / 全加器 / 三输入异或 / 4 位与 netlist+circ
├─ bin/logisim-mcp.js      # npm 启动器（查找 Python 并透传 stdio）
├─ package.json            # npm 包定义（bin / files / 无运行时依赖）
├─ test/smoke.js           # npm 包装冒烟测试（握手 + tools/list + doctor）
└─ tests/selftest.py       # 41 项端到端断言
```

## License

MCP 服务端代码 MIT。Logisim-evolution 本身为 GPLv3，归其开发者所有。
