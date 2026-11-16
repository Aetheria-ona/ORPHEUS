# ORPHEUS

[English](README.md) · [Discord](https://discord.gg/tqukqjAk7) · [Rust 分支](https://github.com/leafall903/ORPHEUS/tree/rust)

ORPHEUS 是面向 **《守望先锋 1》1.74.0.0.104319** 版本的开源服务端复刻与协议研究项目。

- **`main`（当前分支）**：社区维护的 Python 参考服务端、Windows 启动器、数据提取工具与测试套件。
- **`leafall`**：英雄技能开发分支（已与 `main` 同步）。
- **`rust`**：原生 Rust 专用服开发分支（与 `main` 共用跨语言行为契约）。

[本地训练靶场](#本地训练靶场单人测试) · [加入现有服务器](#加入现有服务器) · [当前进度](#当前进度) · [贡献指南](CONTRIBUTING.md) · [问题列表](https://github.com/leafall903/ORPHEUS/issues)

---

## 快速上手

### 本地训练靶场（单人测试）

Windows 本地启动需要准备：
1. 64 位 **Python 3.12**
2. 正版 **1.74.0.0.104319** 游戏客户端
3. 准备好的服务端运行数据包 `runtime-data/`（目录清单见 [data/README.md](data/README.md)）

将运行数据包解压放置在仓库根目录下的 `runtime-data/`（或通过环境变量 `ORPHEUS_RUNTIME_DIR` 指定路径）：

```text
ORPHEUS/
  runtime-data/
    manifest.json
    data/
    bnet/descriptors.pb
    collision/0688.col
```

启动前先运行预检确认文件齐全：

```powershell
.\scripts\START.bat --check-runtime
```

首次运行需编译 Windows 客户端中继 DLL（在安装了 MSVC Build Tools 的 **x64 Native Tools 命令提示符**中执行）：

```bat
.\relay\build.bat
```

启动本地服务端与游戏客户端：

```powershell
.\scripts\START_LOCAL.cmd --game-exe 'C:\path\to\Overwatch'
```

如果只需要无客户端纯启动服务端与 Web 管理面板（`http://127.0.0.1:3725`，Linux / macOS 同样支持）：

```powershell
.\scripts\START_SERVER_LOCAL.cmd
# 或直接运行 Python 入口：
python -m orpheus --mode server
```

### 加入现有服务器

以锦标赛模式连接别人的 ORPHEUS 服务器，只需要原版 104319 客户端与 Python 3.12，不需要本地 `runtime-data/` 和编译中继 DLL：

```powershell
.\scripts\JOIN_SERVER.cmd --server 'HOST:3724' --game-exe 'C:\path\to\Overwatch'
```

完整菜单联机模式（`--mode join`）参数说明可通过 `python -m orpheus --help` 查看。

### 运行公开源码测试（无需游戏本体）

公开测试套全部使用合成 JSON 数据，在 Windows、Linux、macOS 上都能直接运行（清空 `ORPHEUS_RUNTIME_DIR` 与 CI 保持一致）：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -B -m ruff check . --no-cache
.\.venv\Scripts\python.exe -B -m unittest discover
node --test tests/public/test_dashboard_client.mjs tests/public/test_npc_console_client.mjs
```

如需运行依赖真实 104319 图谱/碰撞数据的本地测试（`tests/runtime/`）或提交 PR，请查看 [CONTRIBUTING.md](CONTRIBUTING.md)。

---

## 准备运行数据

如果你已经有本地研究目录，可直接用脚本打包一份干净的 `runtime-data/`：

```powershell
py -3.12 -B tools/prepare_server_runtime.py --source 'C:\path\to\prepared-checkout' --destination .\runtime-data --map 0688
```

详细文件清单、通过 DataTool 生成基础目录（`tools/prepare_client_catalogs.py`）、表情/语音扩展表和射线缓存生成步骤见 [data/README.md](data/README.md)。

---

## 当前进度

| 模块 | 服务端已实现能力 | 104319 客户端实机情况与待办 |
| --- | --- | --- |
| **登录、存档与画廊** | 本地 Battle.net / 大厅认证、玩家设置与按键持久化（`22200`–`22204`）、饰品装备与英雄画廊预览。 | 登录、进图、换皮肤及设置保存均已实机跑通；其余社交/竞技模式规则待完善。 |
| **士兵 76 与靶场机器人** | 完整身体与武器脚本、步枪射击/装填、疾跑、螺旋飞弹、生物力场、靶场 15 个机器人巡逻与击杀重生。 | 机器人可正常受击消灭与刷新；完整 60 秒稳定性验收、敌人头顶血条（#7）、击杀栏显示（#8）和爆头音效（#9）仍待查。 |
| **末日铁拳与破坏球** | 已同步 `leafall` 分支工作：裂地重拳判定/波纹/特效、火箭重拳冲锋/视角/蓄力走速、上勾拳护盾与控制、抓钩、四联火炮、重力坠击、感应护盾、地雷阵。 | 火箭重拳冲锋位移、不掉地下与蓄力移动已实机确认；铁拳伤害（#5、#36、#37）、大招提前落地（#38）、裂地重拳复测（#6）与拳套视角偏移（#47）待解决。 |
| **交流轮盘与语音** | 装备表情加载、第三人称镜头/角度锁定、天使法杖模型切换、装备语音与喷漆同步。 | 天使「放松」「权舞风起」镜头与武器显示已实机确认；带位移表情仍待处理。 |
| **PvE 模式入口** | 行动档案/怪鼠复仇全 47 张难度/挑战卡、教程关与靶场入口与退回菜单流程。 | 47 张卡均已在 104319 实机完成进图与退出；局内任务脚本与敌人 AI 待补全。 |

详细里程碑与具体测试覆盖请看 [docs/ROADMAP.md](docs/ROADMAP.md)、[docs/STATE.md](docs/STATE.md) 和 [CHANGELOG.md](CHANGELOG.md)。

---

## 目录结构

| 路径 | 职责 |
| --- | --- |
| `src/ow174/bnet`、`src/ow174/lobby` | 本地 Battle.net 认证、账号存档、社交与大厅匹配服务 |
| `src/ow174/game`、`src/ow174/game/script` | 加密 UDP 传输、碰撞与角色移动、战斗判定、StateScript 虚拟机与状态同步 |
| `src/ow174/dashboard`、`src/orpheus` | 本地 Web 管理面板与 `orpheus` 命令行入口 |
| `relay/` | Windows C++ 客户端 TLS 中继 DLL 源码 |
| `scripts/` | Windows 批处理启动脚本 |
| `tools/` | DataTool / CASC 提取工具与 `prepare_server_runtime.py` 打包脚本 |
| `fixtures/behavior/` | 与 `rust` 分支共用的跨语言 JSON 行为契约 |
| `tests/public/`、`tests/runtime/` | CI 公开合成测试（`tests/public`）与需本地数据测试（`tests/runtime`） |

## 许可与声明

源码采用 [MIT 协议](LICENSE)，研究来源与致谢见 [docs/CREDITS.md](docs/CREDITS.md)。本项目仅供非官方技术研究与保存用途，严禁向公开仓库提交游戏客户端、商业资源、密钥、密码或原始网络抓包。
