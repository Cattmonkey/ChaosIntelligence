# 版本替换与托管更新

目录项目可直接使用本地新包替换运行环境；独立服务继续使用已有的签名 feed 与托管更新器。先按当前交付方式选择，不混用它们的安装根与配置。

| 当前方式 | 更新入口与边界 |
| --- | --- |
| project ZIP／`chaos.py` 目录项目 | `python3 chaos.py replace NEW.zip`；保留本工程源码、配置和数据库，支持环境回退 |
| SDK 嵌入／portable 包 | 由宿主选择新的完整导入根并重启，见[安装](INSTALLATION.md#作为完整包放进其他项目) |
| 已启用托管安装的独立服务 | `chaos update` 的 feed／验签／空闲切换流程，保留原工作目录与业务数据 |

## 目录项目：直接换新版

在已有工程中首次安装使用 `python3 chaos.py install TARGET`，支持 replace 的 Chaos 项目直接使用 replace。旧项目若没有 replace 命令，可在解压后的新版包目录执行 `python3 chaos.py install /absolute/path/old-project`；对于不支持环境协议的历史启动器，只迁移与旧包完全一致的官方入口；已声明兼容协议的启动器保持原样。其余定制入口拒绝覆盖。回退到不具备环境管理命令的历史运行时后，可继续 run／IDE；再次升级仍从新版包执行 install。请从可信发布者取得完整 project ZIP 或 portable ZIP，先退出本项目的 IDE／后台程序，再在原工程执行：

```sh
python3 chaos.py replace /absolute/path/Chaos-Project-New.zip
python3 chaos.py status
python3 chaos.py --version
python3 chaos.py run
```

同版本完整包可重新安装；比当前版本更旧的包会被拒绝，恢复上一环境使用 rollback。replace 不接受单个 Python 文件、wheel 或“只有改动文件”的补丁包。

macOS／Linux 使用 `python3`，Windows 换为 `py -3`；所有方式均需要 Python 3.9+。replace 使用本地归档，不需要配置在线 feed，不自动下载新版或安装依赖。

替换遵循同一条完整环境路径：

1. 验证归档路径、文件集合、长度、清单和 wheel RECORD 等完整性，拒绝损坏或不完整的包。
2. 将候选运行时放入独立环境并执行语言自检，不向当前运行目录叠加文件。project ZIP 中的初始源码与模板不会覆盖原工程。
3. 仅在依赖需求和 Python／平台指纹匹配时继承现有依赖；不匹配时新环境保持待准备状态，按提示运行 `python3 chaos.py setup`。纯语言核心仍使用 Python 标准库。
4. 校验与候选语言自检成功后原子切换活动环境指针。失败时保留原活动环境；下次仍从工程内同一个 `chaos.py` 启动。

共享环境锁保护通过项目启动器进入的程序；替换和 setup 需要独占环境，已有 IDE 还受原项目锁保护。出现忙碌提示时正常停止进程再重试，不删除锁文件强行切换。

用户源码、`chaos.project.json`、`.chaos/settings.json`、IDE／CLI 数据库与任务数据不参与环境替换。运行时与依赖按环境配对保存；保留当前和上一环境，并保留旧布局的基础 `.chaos/runtime` 兼容目录。不要将业务文件放进这些受管理的运行目录，也不要把新 project ZIP 覆盖解压到原工程。

关闭本项目的 IDE／后台程序后可恢复上一环境：

```sh
python3 chaos.py rollback
python3 chaos.py status
python3 chaos.py
```

rollback 恢复上一运行时及其配套依赖，不回滚源码编辑、配置、数据库或已执行程序的外部效果。替换不会热改已经导入的 Python 模块，启动新版本需要重新运行程序。更换 Python／操作系统／架构后，需要重新检查并准备匹配依赖。

没有 previous 环境时 rollback 会拒绝；上一环境损坏或与当前 Python 不兼容时也不会切换。原始 `.chaos/runtime` 的保留不等于提供任意历史版本选择。当前运行时已经缺失、导致启动器无法导入管理命令时，应从备份恢复完整受管环境后再操作，不能假定 rollback 仍能启动。

清单和 RECORD 用于内容完整性检查，不能替代对发布来源的信任。分发干净的新包，不携带用户密钥或项目状态。目录替换不修改独立服务的托管更新配置；下方机制仍用于原先的托管安装。

## 独立服务：只需一次启用

此节面向已提供 `chaos update` 的独立安装；不提供更新器的旧版本须先通过其原安装方式升级，不能假定会自动接收更新。由维护者提供真实 HTTPS feed 地址和通过可信渠道提供的公钥后执行：

```sh
chaos update configure --feed https://YOUR-UPDATE-HOST/chaos/feed.json --public-key /path/to/maintainer.pub
chaos update adopt
```

`adopt` 下载、验签、在独立 venv 安装和自检，并输出稳定启动器路径。默认位置为 `~/.local/share/chaos-intelligence/app/bin/chaos`（Windows 为 `chaos.cmd`）。把这个 bin 目录置于 PATH 最前，或让现有服务管理器直接执行输出的绝对路径；随后仍使用原来的 `chaos serve --port ... --db ... --config ... --state-dir ...` 参数。服务管理器应管理此启动器，不应直接绑定某一版本的 Python。启动时继续使用原项目工作目录，原有相对路径含义不变。通过 `ServiceSpec(profile="managed", managed_home=...)` 和 `chaos service` 登记操作系统服务时复用这同一个稳定监督器；源工作区的 `source` profile 则明确运行所选源码，使用登记根下独立的 `updaters/<name>/` 保存更新状态，不读取继承环境或全局安装的更新 feed，不自动执行 `adopt`、下载版本或更改当前安装。服务的 plan/install/status/start/stop/uninstall 与所有权规则见[无界面常驻部署](INSTALLATION.md#无界面常驻部署)。

示例（POSIX）：

```sh
export PATH="$HOME/.local/share/chaos-intelligence/app/bin:$PATH"
chaos serve --port 8765 --db /absolute/path/workspace.sqlite3 --state-dir /absolute/path/control
```

更新后台设置保存在安装根下 `updater.json`，与模型密钥配置分离；可用 `--home /absolute/path/app` 指定另一安装根。无界面可通过 `chaos update status`、`chaos update check` 查看状态；`chaos update apply --server http://127.0.0.1:8765` 与 `chaos update rollback --server http://127.0.0.1:8765` 走同一服务接口和空闲切换流程。

## 独立服务的更新事务

1. 异步读取 HTTPS feed，使用安装时固定的 Ed25519 公钥验签；验证 API v1、存储版本 1、安装器版本 1 兼容性，拒绝远端降级。
2. 下载完整 wheel，检查签名内 SHA-256、长度、包版本和归档路径。在 `releases/<version>/venv` 安装全新环境，避免覆盖安装留下已经删除的 Python 或网页文件。
3. 用候选解释器执行语言、HTTP 服务、依赖和静态资源自检。检查通过前不切换指针。
4. 暂停新任务接收和队列派发，等待正在执行的任务自然结束（最多 300 秒，超时恢复正常接收，可稍后重试）。排队任务持久化保留。读取状态、观看执行进度和取消任务仍可使用。
5. 原子切换 `current.json`；原进程退出，监督器按原参数和工作目录启动新版本。健康检查同时匹配版本与进程实例，避免把另一个占用端口的服务误判为成功。
6. 启动健康检查失败则自动恢复前一版本。成功后只保留当前版本与上一版本；手动回滚也使用同一个任务等待与重启流程。

更新期间已有计算会继续运行；切换时会有短暂连接中断。页面自动重连，程序客户端应对 `503 updating` 和连接中断进行业务适当的重试；系统不会自动重发可能造成重复执行的业务 POST。该机制不承诺零停机升级。

## 独立服务的数据与兼容性

安装根只存程序版本、更新配置、临时安装文件和启动器。项目源文件、SQLite 工作区、任务状态、模型密钥和用户工具数据必须保存在安装根之外；默认工作区仍跟随原工作目录，默认模型配置位置不变。不要把用户文件放进 `releases` 或 `staging`，这些目录由更新器管理。

保留已有 `chaos` 命令、Python `Framework`/`Client` 方法和 `/api/v1` 路径。新增接口：

- `GET /api/v1/updates`：真实版本、安装模式、进度、发布者指纹和错误。
- `PATCH /api/v1/updates/config`：平铺 JSON `{version, feedURL, autoCheck, intervalSeconds, channel}`，带版本冲突检查；HTTP 不允许设置发布者公钥。
- `POST /api/v1/updates/check|apply|rollback`：请求体 `{}`，返回 202，后台执行。
- `Client.updates()`、`configure_updates(patch)`、`check_updates()`、`apply_update()`、`rollback_update()`：与页面共用服务。

作为库嵌入其他 Python 进程的 `Framework` 无法在宿主进程内自动替换模块；继续使用原来的包管理方式升级并重启宿主。独立服务的 HTTP 客户端不需要更换调用代码。未来破坏兼容性的存储迁移必须使用新的存储版本，本更新器会拒绝直接安装，不能只修改兼容性声明绕过迁移。

应用 wheel 是完整源码及资源包；第三方依赖在全新 venv 中从 Python 包索引安装，需要网络，不能称为离线全依赖安装包。更新器检测到当前环境已安装 Playwright 时，会为新版本保留 browser 扩展、安装并自检 Chromium，同时保留上一版浏览器缓存供回滚使用；首次未启用浏览器功能的安装仍可按原可选功能说明配置。

## 发布者

修改源码后递增 `chaos/version.py`，使用 [完整发布流程](RELEASING.md) 生成 wheel、源码包、校验和与签名 feed。先上传版本文件，最后替换固定地址的 feed；所有已经配置该 feed 与公钥的安装会自动检测到新版本。尚未指定公网发布地址时页面明确显示「未配置」，不会假装已经连接到发布服务。
