# AutoBench

通过 SSH 自动执行 Linux 性能测试，在一台机器的多个 UEFI 操作系统之间依次切换，归档原始性能数据和运行环境。第一版适配 UnixBench。

## 安装

本地要求 Linux、Python 3.11 或更新版本：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/autobench --help
```

后续示例假设已激活虚拟环境，或已将 `autobench` 安装到 PATH。

每个远程系统需预先准备：

- Python ≥3.8、systemd ≥240、SSH/SFTP、`efibootmgr`。
- UnixBench 所需的 `make`、GCC（或配置的编译器）、Perl 及源码版本要求的其他工具。
- 可登录的 root，或具有非交互免密 sudo 权限的测试用户。管理命令包括 `systemd-run`、`systemctl` 和 `efibootmgr`；测试进程仍以 SSH 登录用户运行。
- 各系统独立的 UEFI 启动项。各个系统的测试目录不必共享。

## 配置与启动

复制 [examples/autobench.yaml](examples/autobench.yaml)，填写机器、用户名、认证方式和启动项；将固定版本 UnixBench 的 tar/tar.gz 源码包保存到本地。支持上游仓库包或只包含 UnixBench 子目录的包，自动查找唯一包含 `Run` 与 `Makefile` 的目录，也可显式指定 `source_subdir`。源码包不允许路径越界、链接、设备或重复文件条目。

配置中所有本地相对路径以 YAML 所在目录为基准。`remote_dir` 相对路径以远程登录用户 HOME 为基准。源码压缩包按校验和缓存。每次运行中，同一机器、系统和测试配置的所有轮次共用一次构建；不同运行、机器、系统或测试配置分别构建。每轮使用独立工作目录，直接运行同一构建目录中的二进制。

每项测试默认开启预热，可在网页中关闭，或设置 `benchmarks[].warmup: false`。每台机器、每个系统下的每项测试先完整预热一次，顺序为：构建 → 稳定等待 → 预热 → 再次稳定等待 → 正式第 1 轮；后续正式轮次仍在执行前稳定等待。预热与正式轮次共用构建，使用相同子测试、参数、环境配置和运行超时，独立准备工作目录、结果目录及默认临时目录。预热输出结束后清理，不下载、不归档，仅保留执行状态、时间和失败原因。`repetitions` 仅计算正式轮次，例如配置 3 轮时实际执行 4 次，只保存 3 轮正式结果；开启预热会增加一次完整测试及一次稳定等待的耗时。预热失败或超时会停止该机器后续测试，其他机器继续执行。

预热配置仅对新运行生效。恢复已有运行沿用冻结配置；旧冻结配置或已提交 manifest 缺少预热字段时保持原有行为，不追加预热或重跑任务。

```bash
autobench discover examples/autobench.yaml --machine server01
autobench validate examples/autobench.yaml
autobench doctor examples/autobench.yaml
autobench run examples/autobench.yaml
```

`discover` 和 `doctor` 只读当前系统，不重启机器，也不会将未访问系统的依赖标为已检查。初次填写配置时，可以先用四位十六进制启动项占位运行 `discover`，根据返回的 `efi.current` 和启动项列表修改配置。系统通过 UEFI 的 BootCurrent 与配置的启动项匹配，重启通过 boot ID 的变化确认。

SSH 默认验证主机密钥。通过可信渠道核对各系统指纹后，将它们登记到指定的 `known_hosts`。同一 IP 对应多个系统时可以登记多个合法主机密钥，或为系统设置 `host_key_alias` 并分别登记对应别名。工具不自动接受新密钥，不读取用户 SSH config。

同一设备的多个系统共用 IP 时，也可在机器的 `ssh` 中关闭主机密钥校验：

```yaml
ssh:
  host: 192.168.1.100
  username: bench
  verify_host_key: false
```

`verify_host_key` 默认 `true`，仅接受 YAML 布尔值，不接受字符串、数字或 `null`。机器级配置由各系统继承；系统的 `ssh.verify_host_key: true` 可重新启用校验，省略则继承。关闭后不校验 SSH 服务器主机密钥，不读取 `known_hosts`，也不应用 `host_key_alias`；原有文件路径和别名设置仍保留。密码、私钥、SSH Agent 认证及 UEFI 启动项校验照常执行。

此选项同时作用于直连、SOCKS5、SSH 命令和 SFTP，包括发现、诊断、测试、恢复和结果收集。关闭时不要求 `known_hosts` 文件存在，但私钥文件仍需有效。恢复使用 SQLite 中冻结的设置；修改 YAML 只影响新运行，旧冻结配置缺少该字段时仍按默认值校验。


密码登录直接在 YAML 的 `ssh.password` 中填写字符串，建议加引号；密码按字面值使用，不进行变量替换或 Shell 展开。公钥登录配置 `client_keys`，或启用 `agent` 使用本地 ssh-agent；加密私钥可先加载到 ssh-agent。每个系统的 `ssh` 可以覆盖机器默认设置；切换认证方式时可用 `password: null` 清除继承的密码、`client_keys: []` 清除密钥列表。仅使用密码认证时，设置 `client_keys: []` 和 `agent: false`。

```yaml
ssh:
  host: 192.0.2.10
  username: bench
  password: "your-ssh-password"
  known_hosts: ~/.ssh/known_hosts
  client_keys: []
  agent: false
```

密码随完整配置保存在本地 SQLite 中，供恢复运行使用；状态目录权限为 0700，数据库权限为 0600。结果归档的 `config.json` 将所有机器级、系统级 SSH 和代理密码替换为 `********`，保留显式的 `null`。密码不转发给远端测试进程。

## SSH SOCKS5 代理

在机器的 `ssh.proxy` 中配置已有 SOCKS5 服务，所有 SSH 命令、SFTP 上传下载和断线重连都会经过该代理。未配置或设置为 `null` 时直连。

```yaml
machines:
  - id: server01
    ssh:
      host: target.internal
      username: bench
      known_hosts: ~/.ssh/known_hosts
      agent: true
      proxy:
        host: 127.0.0.1
        port: 1080
        # 代理需要用户名密码时，同时填写下面两项：
        # username: proxy-user
        # password: "your-proxy-password"
    systems:
      - id: ubuntu
        boot_entry: "0001"
        # 未填写 ssh.proxy，继承机器的代理。
      - id: debian
        boot_entry: "0002"
        ssh:
          proxy: null  # 此系统直连；也可填写完整代理对象来替换机器的代理。
```

代理 `host` 必填，`port` 默认 `1080`，范围为 1–65535。无认证代理省略 `username` 和 `password`；认证代理必须同时配置这两项，密码直接填写在 YAML 中。由于代理库的编码限制，代理用户名和密码各为 1–255 个 ASCII 字符。代理认证独立于 SSH 登录认证。

目标域名由代理解析，启用主机密钥校验时 `known_hosts` 仍需登记目标主机及端口或 `host_key_alias` 对应的密钥，不能以代理主机的密钥替代。代理建立连接与 SSH 握手共享 `policy.connect_timeout`（默认 15 秒），已连接后的命令和传输沿用原超时策略。代理不可用时报告代理连接失败并沿用现有重连策略，不回退直连。

`discover`、`doctor`、`run`、`resume`、`fetch` 和 `cancel` 自动使用代理配置。`resume` 使用 SQLite 中冻结的代理设置及密码；修改 YAML 仅影响新运行。系统级代理对象会整体替换机器级代理，不进行对象内部字段合并。结果归档中的代理密码已脱敏，代理凭据不会传给远端测试进程。

工具连接已有 SOCKS5 服务，不自动启动 `ssh -D`，不读取用户 SSH config 或全局代理环境变量，不支持 HTTP/SOCKS4 代理或代理链。

## 构建与运行环境变量

```yaml
machines:
  - id: server01
    ssh:
      host: 192.0.2.10
      username: bench
      known_hosts: ~/.ssh/known_hosts
      agent: true
    systems:
      - id: ubuntu
        boot_entry: "0001"
        env:
          PATH: /usr/local/bin:/usr/bin:/bin
          MALLOC_ARENA_MAX: "2"

benchmarks:
  - id: unixbench-default
    source_archive: ./sources/unixbench.tar.gz
    make_args: ["CC=gcc", "CFLAGS=-O2"]
    args: ["-c", "1", "-c", "8"]
    tests: []  # 可选，例如 ["execl", "shell"]
    env:
      MALLOC_ARENA_MAX: "4"
      OMP_NUM_THREADS: "8"
      EMPTY_VALUE: ""
```

构建和运行均使用 **systemd 服务基础环境 → 系统 `env` → 测试 `env`** 的覆盖优先级。上述构建和测试进程收到的 `MALLOC_ARENA_MAX` 均为 `4`，构建阶段的工具链版本采集也使用合并后的环境。基础环境来自 systemd 托管服务，不是交互式 SSH 登录 Shell；不会自动读取 `.bashrc`、激活 virtualenv 或继承本地控制端环境。

通过 `tests` 指定要运行的 UnixBench 测试项，支持单项、多个测试项和原生组合名。例如：

```yaml
tests: ["execl", "shell"]
args: ["-c", "1", "-i", "3"]
```

执行命令为 `./Run -c 1 -i 3 execl shell`，其中 `shell` 包含 `shell1`、`shell8`、`shell16`。也可分别指定这些子项，或使用 `fs`、`system`、`index` 等组合。多个测试项在同一个任务中执行，不拆分调度任务。测试名必须是非空字符串，不能包含空白、NUL 或以 `-` 开头；具体名称是否受支持由源码包内的 UnixBench 判断。

`tests` 缺省或为空时保持原有行为。`args` 中已有的测试名继续生效，`tests` 追加在 `args` 后面；两处均未指定测试名时运行默认的标准 `index` 测试。清空网页选择仅清空 `tests`，不会移除 `args` 中的测试名。

变量名须匹配 `[A-Za-z_][A-Za-z0-9_]*`，值须是字符串；数字和布尔值需要加引号。允许空字符串，不允许 NUL。值不执行 Shell 展开：`$PATH`、`${VAR}`、`$(command)`、反引号均为原样字符。需要扩展 PATH 时请填写完整路径。

系统和测试 `env` 同时传给构建与测试进程。编译参数仍可使用 `make_args`；编译变量最终遵循 Makefile 和 make 命令行的优先级，传入环境变量不保证覆盖 Makefile 内的赋值，需要覆盖时可通过 `make_args` 传入 `CC=gcc`、`CFLAGS=-O2` 等命令行参数。`UB_RESULTDIR`、`UB_BINDIR` 和 `MAKE` 为工具保留，配置它们会报字段错误；工具只在运行阶段设置这些变量及默认临时目录。运行时通过 `MAKE` 检查入口校验既有产物，避免 UnixBench 内部 `make check/all` 重新编译；不修改上游源码。UnixBench 自身可能覆盖 `LANG`，归档记录的是启动环境，不能将其理解为工具内部所有子进程的最终环境。

已完成并封存的构建继续复用，不因环境传递规则更新而自动重建；新执行的构建使用上述合并规则。

按配置要求，完整环境变量值会原样保存到归档；归档目录为 0700，工具创建的元信息和下载文件为 0600。

## 状态、恢复和取消

`run` 在开始时打印运行名称（Run ID）和状态目录；运行名称如 `2026-11-03_14-30-25`。本地默认状态目录为 `~/.local/state/autobench`。如果配置了其他 `state_dir`，后续命令需提供同一个 `--state-dir`。

```bash
autobench status 2026-11-03_14-30-25
autobench resume 2026-11-03_14-30-25
autobench fetch 2026-11-03_14-30-25
autobench cancel 2026-11-03_14-30-25
# 自定义状态目录：
autobench resume 2026-11-03_14-30-25 --state-dir /path/to/state
```

- SSH 断线不会停止 systemd 托管的编译或测试。收到启动响应前断线，控制端查询原 job 的状态，不创建新任务。
- 本地退出或 Ctrl-C 后，远程测试继续。`resume` 使用 SQLite 中冻结的配置，包括原密码和构建、测试共用的环境变量；修改 YAML 只影响新运行。
- `fetch` 只收集已经封存的产物，不启动测试、不切系统。恢复时若任务所属系统未启动，暂停并报告，由用户先恢复该系统。
- 远端意外重启、编译失败或运行失败均保留日志，不自动重跑；后续系统暂停，其他机器继续。需要重新测试时创建新的 run。
- 取消请求先持久化。已有控制端运行时由其轮询处理；远程不可达时保留 `cancel_pending`，连接恢复后重新执行 `cancel` 或 `resume`。已提交的系统重启不会被取消命令撤销。
- 启动是否提交无法确认时标为暂停，不通过重复提交来猜测。可检查远端 job 的 `submitted.json`、`state.json` 及 `systemctl status autobench-JOB_ID.service` 后处理。
- 系统切换意图在本地先持久化。重启响应丢失后等待原启动；启动超时不会重发重启命令。人工完成/修复原启动后再 `resume`，工具核对新 boot ID 和目标 UEFI 启动项。
- 成功或失败产物均通过 SFTP 回传。下载中断后从已保存的文件偏移继续；哈希错误会重传损坏的临时文件。已提交的本地归档若被外部修改，则暂停，修复或移开损坏的 job 目录后再 `fetch`。

只有结果完整回收并确认后才切换下一个系统。工具只设置一次性 BootNext，不修改 BootOrder；已有 BootNext、错误系统或启动超时都会停止该机器的推进。最终停留在最后一个测试系统，远端产物保留供排查。

同一控制端使用机器 ID 加锁，因此不同配置描述同一物理机器时应使用同一个 ID、同一个状态目录。远端也会锁住运行中的任务并保留待采集任务的占用记录。第一版不提供多控制端跨操作系统的分布式协调，不提供带外电源恢复或 GRUB 菜单控制。

## 结果目录

```text
results/2026-11-03_14-30-25/
  config.json
  events.json
  server01/kylin/unixbench/
    build.stdout / build.stderr # 一次构建的日志
    build.json                  # 构建身份、命令、时间、退出状态
    build-environment.json      # 实际传入构建进程的完整环境
    toolchain.json
    round-001/
      results/                  # 本轮 UnixBench 原始报告
      run.stdout / run.stderr
      run-environment.json      # 实际传入本轮测试进程的完整环境
    round-002/
      ...
```

运行名称采用控制端本地时间，格式为 `YYYY-MM-DD_HH-mm-ss`；同秒重名时依次追加 `-02`、`-03` 等序号。创建时检查状态库，并原子创建结果根目录预留名称，避免覆盖已有结果。运行名称创建后固定，恢复时不重新生成。

归档路径为 `<运行名称>/<机器 ID>/<系统 ID>/<测试 ID>/round-NNN/`，三个 ID 分别取自配置的 `machine.id`、`system.id`、`benchmark.id`。轮次从 `round-001` 开始，至少补齐三位，超过 999 时自然扩展。同一个任务选择的多个 UnixBench 子测试归入同一轮次目录。公共文件先下载到相邻的 `<测试 ID>.partial/` 并原子提交，后续轮次只核验已存在的公共文件；每轮下载未完成时使用相邻的 `round-NNN.partial/`。公共及本轮文件都校验完成后才确认采集。内部 Job ID 仍使用 UUID，用于远端执行和恢复，不作为结果目录名。本版本面向全新部署，不兼容旧状态、远端任务和旧归档格式，不执行历史迁移。

公共 `build-environment.json` 保存实际传入构建进程的完整环境；每轮 `run-environment.json` 直接保存实际传入本轮测试进程的完整环境变量表，包括空字符串、本轮结果路径、临时目录和 systemd 环境变量，无需合并其他文件。记录的是启动环境，不跟踪 UnixBench 启动后自行修改的变量。运行环境准备完成后，测试失败或取消时仍保留该文件；若在准备运行环境前已失败或取消，则不生成。公共构建归档封存后不再改变。

构建成功后在远端内部 `ready.json` 记录二进制清单和原 boot ID，每轮运行前后校验；下载通过远端内部 `artifacts.json` 提供的清单校验文件大小和 SHA-256，结果目录与 ZIP 不输出独立校验清单或源码元信息文件。产物缺失、增加、内容变化或系统意外重启时停止该机器，不自动重建；构建失败、中断和取消时仍保留已有公共日志。控制端断线不影响远端构建、稳定等待或测试，恢复时查询原任务，不重复提交。

框架负责构建、执行测试和归档结果，不主动采集用于性能分析或优化的系统快照，也不解析 UnixBench 分数。UnixBench 原生报告、构建与运行日志、工具链及启动环境记录会保留。`BenchmarkAdapter` 提供依赖、执行命令和工具元信息接口；SSH、环境合并、生命周期和归档由公共执行层管理。

默认并发 4 台机器；轮询 10 秒，预热及每轮正式测试前稳定等待 60 秒，首轮先完成构建再等待；重连等待 15 分钟，启动等待 10 分钟，编译 30 分钟，每次预热或正式测试分别限时 6 小时。重连按指数退避，最大间隔 30 秒并加入抖动。UnixBench 默认标准 `./Run`，预热一次、正式测试一次。所有时间与并发限制可通过配置调整。存在失败或未完成任务时命令返回非零；取消命令在所有任务收尾完成后返回成功。

## 测试

```bash
pytest -q
ruff check src tests
```

默认测试覆盖真实本地 SSH 密码/公钥认证和 SFTP、网络中断、断点续传、哈希校验、独立进程执行与取消、环境变量、调度和恢复；这些测试不重启机器。远端脚本另有 Python 3.8 语法兼容性检查。

以下验收入口只在显式设置环境变量时启用，使用指定配置真实运行测试。常规 SSH/systemd 验收的配置必须只包含一台机器的当前系统；UEFI 验收会按配置重启专用测试机，可使用 OVMF 双 Linux 虚拟机：

```bash
AUTOBENCH_INTEGRATION_CONFIG=/absolute/current-os.yaml pytest -q -m integration
AUTOBENCH_UEFI_CONFIG=/absolute/two-os.yaml pytest -q -m uefi
```

配置中的源码包决定验收使用真实 UnixBench 还是短时测试夹具。完整验收应使用固定版本的真实 UnixBench，并在运行过程中断开网络或停止本地控制端，再恢复连接运行 `resume`，核对每个 job 只执行一次且结果完整。硬件测试需要可用的目标机器，不会在默认测试中自动执行。

开发验证已使用上游 `v6.0.1` 源码，通过独立 worker 完成 `make GRAPHIC_TESTS=` 和 `./Run -i 1 -c 1 dhry2reg`，生成原始文本、日志及 HTML 报告并封存哈希清单。这是短时适配冒烟验证，不代表完整 UnixBench 或真实 UEFI 切换验收已完成。

## Web 管理界面

安装后运行中文测试工作台，无需 Node.js 或外部前端资源：

```bash
autobench web --open-browser
autobench web --config examples/autobench.yaml --state-dir ~/.local/state/autobench
# 允许其他电脑使用服务器实际 IP 访问：
autobench web --host 0.0.0.0 --port 8765
# 指定 IPv4 或监听所有 IPv6 网卡：
autobench web --host 192.168.1.100
autobench web --host ::
```

默认地址为 `http://127.0.0.1:8765`，默认仅监听本机，无需登录。IPv6 URL 使用方括号，如 `http://[::1]:8765`。通配监听时 `--open-browser` 打开本机回环地址；指定 IP 时打开该 IP。`--state-dir` 决定初始浏览的历史目录，新测试仍使用 YAML 的 `policy.state_dir`。

### 创建与修改配置

所有配置统一按五步引导编辑：**连接机器 → 选择系统 → 设置测试 → 运行设置 → 检查运行**。新建默认保存到服务启动目录的 `autobench.yaml`，可点击文件名更改位置。打开、导入和刷新已有 YAML 均从第一步开始，可直接点击任一步骤修改，无需逐步重填。

引导配置中的测试计划会实时汇总任务数、系统执行顺序与测试轮次；预览只读取当前配置，不向 YAML 写入额外字段。桌面使用双栏布局，平板和手机按宽度折叠导航与表单，手机上的运行记录显示为包含状态、更新时间和详情操作的列表。字体随安装包提供，无需连接外部字体服务。

- **机器连接**：地址、端口和用户名紧凑排列。默认选择密码认证；密钥认证可选择服务器私钥文件或 SSH Agent。切换单一认证方式会清理冲突项，现有混合认证保留为“自定义认证（高级）”。主机密钥文件、代理、远端目录等位于折叠的高级设置。连接机器和选择系统两步均支持添加、排序和移除机器。
- **连接测试**：填写连接信息后，可在保存前测试 SSH、UEFI、运行环境、基础命令和可用空间。按钮右侧在检测中显示旋转图标，全部通过后显示绿色对号；失败时显示红叉及所有失败项目和原因。修改连接或路径基准后需重新测试。系统手动添加。
- **系统设置**：启动项可手动填写，也可点击输入框右侧的“自动识别”。识别中显示旋转图标，成功后显示绿色对号并将输入框切换为启动项下拉框，选择后更新配置；失败时显示红叉及原因，保留原控件和原值。识别使用此系统生效的连接设置，只读取启动项。默认沿用机器连接和认证，只在有差异时启用对应的“单独设置”。恢复继承只移除该组覆盖字段，不影响其他覆盖；代理可分别选择继承、直连或指定代理。系统顺序可调整。
- **性能测试**：支持添加、排序和移除测试配置，常用项为源码包、测试项、正式重复次数和默认开启的预热开关；测试计划与检查运行页面分别显示正式轮数和预热设置。可多选系统测试项及组合，也可手动添加其他测试名。未指定测试名时执行标准完整 `index` 测试；`shell` 组合包含 `shell1`、`shell8`、`shell16`。编译和运行参数按独立 argv 项编辑，不进行 Shell 拆分；环境变量、源码校验和超时位于高级设置。
- **运行设置**：集中配置结果输出目录和并行机器数；状态目录、轮询间隔、系统稳定等待、重连／启动／SSH 连接超时及配置版本位于高级调度设置。
- **文件选择**：YAML、源码包、密钥及目录均可通过服务器文件选择器选择，也可手动输入路径。这里的路径属于运行 Web 服务的服务器；不提供源码包上传。浏览器可导入 YAML 内容，再指定服务器上的保存位置。

文件操作菜单提供打开、导入、保存草稿、另存为和下载 YAML。可随时切换“引导配置 / YAML 源码”，返回引导时保留当前步骤。仅展开设置不会补写默认值；未修改的高级值、部分继承、显式 `null`、空列表和空字符串保留原语义。直接编辑并保存 YAML 保留注释；修改可视化配置后重新生成 YAML。错误摘要会切换到对应步骤并定位字段，必要时自动展开高级设置；未知字段在 YAML 源码中修正。

配置内部相对路径始终以 YAML 所在目录为基准；连接测试与启动项识别使用拟保存路径的目录，未指定时使用服务启动目录。保存采用原子替换及 `0600` 权限；外部修改或另存为目标已存在时拒绝覆盖，请重新加载或选择新路径。默认校验主机密钥，须预先核验并登记。可在机器高级设置开启“忽略主机密钥检查”，系统高级设置可选择继承、启用或忽略；忽略时隐藏文件及别名输入但保留其值，连接区域会注明当前状态。

### 检查、执行与结果

“保存并检查”校验模型、源码包及本地 SSH 文件，不会启动测试。通过后点击“开始测试”。允许单独保存未完成草稿；实际执行使用已保存文件并核对内容版本。连接机器和选择系统步骤中的“连接测试”可检查当前 SSH 连接及运行环境，不会切换操作系统。顶部“测试配置”和“运行记录”标签可直接切换页面，切换时保留当前配置草稿、向导步骤和运行详情，刷新后恢复上次选择的页面。

运行记录支持状态筛选、搜索、任务概况、当前阶段、失败原因、恢复控制、收集和取消。历史位置默认显示摘要，可更改至其他 CLI 状态目录。任务按机器和系统分组，原始事件可展开；结果按报告、运行日志、编译日志、环境信息分类，支持文本预览及单文件／ZIP 下载。HTML 报告作为附件下载，文本预览最多 256 KiB。

关闭浏览器不停止后台调度；刷新后可恢复已保存的配置及选中的运行。退出 Web 服务只停止本地控制器，远端任务继续。重新启动后在同一状态目录选择运行并“恢复控制”，或使用 `autobench resume RUN_ID --state-dir PATH`。取消请求未完成时可继续处理取消；失败测试不会自动重跑，应创建新运行。草稿密码、探测结果不会保存到浏览器存储；运行状态、事件和产物继续使用既有 SQLite 与结果目录。

### 浏览器验收

后端与 CLI 检查仍使用 `pytest -q`。可重复的 Chromium 验收使用模拟远端，不连接或重启真实机器：

```bash
python -m pip install -e '.[test,browser-test]'
python -m playwright install --with-deps chromium
AUTOBENCH_BROWSER_TESTS=1 pytest -q -m browser
```

浏览器验收覆盖引导创建、认证互斥、连接测试状态与失败反馈、手动配置系统、启动项自动识别与选择、服务器文件选择、五步配置及 YAML 往返、机器和测试排序删除、运行设置错误定位、并发编辑保护、字段定位、运行取消、结果下载和移动端布局。CI 单独执行此组验收，普通 `pytest` 默认跳过浏览器及未配置的真实硬件验收。
