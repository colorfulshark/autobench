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
- 各系统独立的 UEFI 启动项和不同的 `/etc/machine-id`。各个系统的测试目录不必共享。

## 配置与启动

复制 [examples/autobench.yaml](examples/autobench.yaml)，填写机器、用户名、认证方式、启动项和 machine-id；将固定版本 UnixBench 的 tar/tar.gz 源码包保存到本地。支持上游仓库包或只包含 UnixBench 子目录的包，自动查找唯一包含 `Run` 与 `Makefile` 的目录，也可显式指定 `source_subdir`。源码包不允许路径越界、链接、设备或重复文件条目。

配置中所有本地相对路径以 YAML 所在目录为基准。`remote_dir` 相对路径以远程登录用户 HOME 为基准。默认源码包和编译产物位于远端每个 job 的独立目录，不复用跨系统二进制。

```bash
autobench discover examples/autobench.yaml --machine server01
autobench validate examples/autobench.yaml
autobench doctor examples/autobench.yaml
autobench run examples/autobench.yaml
```

`discover` 和 `doctor` 只读当前系统，不重启机器，也不会将未访问系统的依赖标为已检查。初次填写配置时，可以先用格式正确的占位 machine-id 运行 `discover`，根据返回的 `machine_id`、`efi.current` 和启动项修改配置。其他系统的 machine-id 需在其已启动时读取。

SSH 必须验证主机密钥。通过可信渠道核对各系统指纹后，将它们登记到指定的 `known_hosts`。同一 IP 对应多个系统时可以登记多个合法主机密钥，或为系统设置 `host_key_alias` 并分别登记对应别名。工具不自动接受新密钥，不读取用户 SSH config。

密码登录使用 `password_env` 引用本地环境变量，或使用 `prompt_password: true` 隐藏输入；两者互斥。公钥登录配置 `client_keys`，或启用 `agent` 使用本地 ssh-agent；加密私钥可先加载到 ssh-agent。每个系统的 `ssh` 可以覆盖机器默认设置；切换认证方式时可用 `password_env: null` 清除继承的密码引用、`client_keys: []` 清除密钥列表。凭据只在内存解析，不写入 SQLite 或归档，也不转发给测试进程。

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
        # password_env: AUTOBENCH_PROXY_PASSWORD
    systems:
      - id: ubuntu
        boot_entry: "0001"
        machine_id: "11111111111111111111111111111111"
        # 未填写 ssh.proxy，继承机器的代理。
      - id: debian
        boot_entry: "0002"
        machine_id: "22222222222222222222222222222222"
        ssh:
          proxy: null  # 此系统直连；也可填写完整代理对象来替换机器的代理。
```

代理 `host` 必填，`port` 默认 `1080`，范围为 1–65535。无认证代理省略 `username` 和 `password_env`；认证代理必须同时配置这两项，密码从指定的本地环境变量读取。由于代理库的编码限制，代理用户名和密码各为 1–255 个 ASCII 字符。代理认证独立于 SSH 登录认证。

目标域名由代理解析，`known_hosts` 仍需登记目标主机及端口或 `host_key_alias` 对应的密钥，不能以代理主机的密钥替代。代理建立连接与 SSH 握手共享 `policy.connect_timeout`（默认 15 秒），已连接后的命令和传输沿用原超时策略。代理不可用时报告代理连接失败并沿用现有重连策略，不回退直连。

`discover`、`doctor`、`run`、`resume`、`fetch` 和 `cancel` 自动使用代理配置。`resume` 使用冻结的代理设置，并重新读取密码环境变量；修改 YAML 仅影响新运行。系统级代理对象会整体替换机器级代理，不进行对象内部字段合并。配置快照和归档只记录密码环境变量名，不记录解析后的密码，也不将代理凭据传给远端测试进程。

工具连接已有 SOCKS5 服务，不自动启动 `ssh -D`，不读取用户 SSH config 或全局代理环境变量，不支持 HTTP/SOCKS4 代理或代理链。

## 运行环境变量

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
        machine_id: "11111111111111111111111111111111"
        env:
          PATH: /usr/local/bin:/usr/bin:/bin
          MALLOC_ARENA_MAX: "2"

benchmarks:
  - id: unixbench-default
    source_archive: ./sources/unixbench.tar.gz
    make_args: ["CC=gcc", "CFLAGS=-O2"]
    args: ["-c", "1", "-c", "8"]
    env:
      MALLOC_ARENA_MAX: "4"
      OMP_NUM_THREADS: "8"
      EMPTY_VALUE: ""
```

优先级为 **systemd 服务基础环境 → 系统 `env` → 测试 `env`**。上述测试收到的 `MALLOC_ARENA_MAX` 为 `4`。基础环境来自 systemd 托管服务，不是交互式 SSH 登录 Shell；不会自动读取 `.bashrc`、激活 virtualenv 或继承本地控制端环境。

变量名须匹配 `[A-Za-z_][A-Za-z0-9_]*`，值须是字符串；数字和布尔值需要加引号。允许空字符串，不允许 NUL。值不执行 Shell 展开：`$PATH`、`${VAR}`、`$(command)`、反引号均为原样字符。需要扩展 PATH 时请填写完整路径。

运行 `env` 仅传给测试进程；构建使用服务基础环境，编译参数使用 `make_args`。`UB_RESULTDIR` 为工具保留，配置它会报错。UnixBench 自身可能覆盖 `LANG`，归档记录的是启动环境，不能将其理解为工具内部所有子进程的最终环境；工具不修改 UnixBench 源码。

按配置要求，完整环境变量值会原样保存到归档；归档目录为 0700，工具创建的元信息和下载文件为 0600。

## 状态、恢复和取消

`run` 在开始时打印 run ID 和状态目录；本地默认状态目录为 `~/.local/state/autobench`。如果配置了其他 `state_dir`，后续命令需提供同一个 `--state-dir`。

```bash
autobench status RUN_ID
autobench resume RUN_ID
autobench fetch RUN_ID
autobench cancel RUN_ID
# 自定义状态目录：
autobench resume RUN_ID --state-dir /path/to/state
```

- SSH 断线不会停止 systemd 托管的编译或测试。收到启动响应前断线，控制端查询原 job 的状态，不创建新任务。
- 本地退出或 Ctrl-C 后，远程测试继续。`resume` 使用 SQLite 中冻结的配置，包括原环境变量；修改 YAML 只影响新运行。密码引用在恢复时重新解析。
- `fetch` 只收集已经封存的产物，不启动测试、不切系统。恢复时若任务所属系统未启动，暂停并报告，由用户先恢复该系统。
- 远端意外重启、编译失败或运行失败均保留日志，不自动重跑；后续系统暂停，其他机器继续。需要重新测试时创建新的 run。
- 取消请求先持久化。已有控制端运行时由其轮询处理；远程不可达时保留 `cancel_pending`，连接恢复后重新执行 `cancel` 或 `resume`。已提交的系统重启不会被取消命令撤销。
- 启动是否提交无法确认时标为暂停，不通过重复提交来猜测。可检查远端 job 的 `submitted.json`、`state.json` 及 `systemctl status autobench-JOB_ID.service` 后处理。
- 系统切换意图在本地先持久化。重启响应丢失后等待原启动；启动超时不会重发重启命令。人工完成/修复原启动后再 `resume`，工具核对新 boot ID 和目标身份。
- 成功或失败产物均通过 SFTP 回传。下载中断后从已保存的文件偏移继续；哈希错误会重传损坏的临时文件。已提交的本地归档若被外部修改，则暂停，修复或移开损坏的 job 目录后再 `fetch`。

只有结果完整回收并确认后才切换下一个系统。工具只设置一次性 BootNext，不修改 BootOrder；已有 BootNext、错误系统或启动超时都会停止该机器的推进。最终停留在最后一个测试系统，远端产物保留供排查。

同一控制端使用机器 ID 加锁，因此不同配置描述同一物理机器时应使用同一个 ID、同一个状态目录。远端也会锁住运行中的任务并保留待采集任务的占用记录。第一版不提供多控制端跨操作系统的分布式协调，不提供带外电源恢复或 GRUB 菜单控制。

## 结果目录

```text
results/RUN_ID/
  config.json
  events.json
  MACHINE/SYSTEM/JOB_ID/
    results/                    # UnixBench 原始报告
    build.stdout / build.stderr
    run.stdout / run.stderr
    environment.json            # 实际传入测试进程的完整环境
    environment-overrides.json  # 系统、测试和工具生成值的来源
    build-environment.json
    system-before.json / system-after.json
    source-metadata.json / toolchain.json
    task.json                   # 执行清单、时间、退出码和状态
    archive-manifest.json       # 文件大小与 SHA-256
```

可选系统信息采集失败会记录到 `errors`，不丢弃性能结果。第一版不解析 UnixBench 分数。`BenchmarkAdapter` 提供依赖、执行命令和工具元信息接口；SSH、环境合并、生命周期和归档由公共执行层管理。

默认并发 4 台机器；轮询 10 秒，系统就绪后稳定等待 60 秒；重连等待 15 分钟，启动等待 10 分钟，编译 30 分钟，测试 6 小时。重连按指数退避，最大间隔 30 秒并加入抖动。UnixBench 默认标准 `./Run`，重复一次。所有时间与并发限制可通过配置调整。存在失败或未完成任务时命令返回非零；取消命令在所有任务收尾完成后返回成功。

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
