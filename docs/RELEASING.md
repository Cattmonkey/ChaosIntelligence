# 发布完整更新包

每次更新都发布完整 wheel：Python 模块、网页资源、受控的 DeepSeek／OpenUI vendor、文档和示例。安装端使用独立版本目录，删除的源码不会留在新版本中。数据目录中的模型配置、密钥和工作区不属于发布包。

## 先选择用户收到什么

| 制品 | 用途与依赖 |
| --- | --- |
| wheel | 安装或托管更新原运行时、网页、文档、示例；依赖由目标Python环境准备 |
| portable SDK ZIP | 解压供宿主导入或用 `run_chaos.py` 调用原 CLI；也可作为目录项目 replace 的完整载荷 |
| project ZIP | 根 `chaos.py`、项目标记、默认源码及初始运行环境；支持安装到已有工程或作为 replace 载荷，Python 3.9+ 仍需用户提供 |

在该版本源码环境或匹配版本的 CLI 中，从已经验证的 wheel 生成目录项目：

```sh
chaos package project /absolute/path/chaos_intelligence-X.Y.Z-py3-none-any.whl --output /absolute/path/chaos-project-X.Y.Z.zip
```

将 `X.Y.Z` 换成实际版本。构建器拒绝覆盖既有输出，项目模板必须与 wheel 中的启动器匹配。project ZIP 的根 `README.md` 来自 wheel 内登记的[目录包速查](PROJECT_README.md)，逐字复制，不另写一份安装说明或重写 Markdown 链接。维护这份文档后须重新构建 wheel，再派生 project ZIP。

project ZIP 不包含 Python、发布者设置、密钥、用户数据库或所有第三方依赖。setup 为目标环境显式准备依赖；运行环境和依赖与用户源码、配置、状态分开保存。project／portable ZIP 的清单和校验和用于检查完整性，不提供发布者身份认证；分发渠道仍须可信。本地 package 命令不自动签名、上传或公开发布。

发布前在 checkout 外完成以下验收，保留命令、返回值和使用的制品哈希：

1. 解压新 project ZIP，执行 run／check／fmt；纯语言路径应在未安装全局 Chaos 的环境中可用。
2. install 到含其他文件的工程，确认没有覆盖宿主文件；执行 setup，启动 IDE 并访问实际地址的 `/docs`，再正常关闭。
3. 使用真实上一版交付包及已有源码／配置／状态，验证迁移、替换、回退、再升级与目录搬移。环境必须完整切换，旧版已删除的模块不能残留在活动运行时。
4. 核对用户源码、配置和数据库在替换前后的哈希；测试运行中替换、损坏包、不相容 Python／环境协议和候选自检失败均保留原可用环境。
5. 检查新包的网页、文档、示例和标准库与该版本源码一致。跨系统和硬件功能只报告实际执行过的验收范围。

目录项目停止后使用 `python3 chaos.py replace NEW.zip` 选择新版，再按原命令启动；禁止将新 project ZIP 覆盖解压到旧工程。环境选择与旧项目迁移详见[更新说明](UPDATES.md)。

## 首次准备签名密钥

在仓库外保存维护者私钥，并通过可信渠道把对应公钥提供给安装者。私钥不得上传到更新服务器、提交 Git 或放入 release 输出目录。下面路径是示例，可替换为自己的安全目录：

```sh
python -m chaos.release keygen \
  --private-key "$HOME/.config/chaos-release/maintainer.pem" \
  --public-key "$HOME/.config/chaos-release/maintainer.pub"
```

生成命令拒绝覆盖已有密钥；POSIX 下私钥权限为 `0600`，构建时也会检查这一点。安装者第一次配置更新时固定公钥，后续不会从 feed 下载或自动信任新公钥。仓库不带可冒充正式发布源的默认签名密钥。

## 构建和签名

维护者的 wheel 构建环境要求 `setuptools>=77`，以保留 PEP 639 的分层许可证路径，避免不同第三方组件的同名 LICENSE 相互覆盖。可在构建虚拟环境执行 `python -m pip install "setuptools>=77" wheel`；运行时仍支持 Python 3.9+，此要求不意味着用户运行目录包时需要升级系统构建工具。

先显式修改 `chaos/version.py` 中的版本号（稳定版 `X.Y.Z`，至少 `0.6.0`）。构建器不会替你修改版本。已交付的版本文件保持不可变：代码、文档或示例任一变化，都发布新版本；修正文档也应递增补丁号，而不是用同一版本号替换不同内容。保留上一版完整制品用于迁移与回退验收。确保当前 Python 环境已安装项目依赖以及 `setuptools>=77`、`wheel` 和 `pip`，然后运行：

```sh
python -m chaos.release build \
  --output dist/releases \
  --base-url https://YOUR-UPDATE-HOST/chaos \
  --private-key "$HOME/.config/chaos-release/maintainer.pem" \
  --include-source
```

`--base-url` 是准备上传文件的 HTTPS 目录，必须替换成实际地址。运行命令不会部署、上传或创建公网服务；成功输出中的 `published: false` 表示文件只在本地准备完成。也可以从源码使用 `python scripts/release.py`，其参数一致。

默认流程：

1. 在临时目录复制白名单源码；排除 Git、`.chaos`、`.development`、隐藏文件、密钥、缓存、旧构建和包元数据，并拒绝源码符号链接。
2. 在隔离的 HOME 和不带模型密钥的进程环境里运行完整 `unittest` 套件；失败就停止。只有已另外验证通过时才使用显式 `--skip-tests`。
3. 在临时目录离线构建完整 wheel，逐文件核对 Python、web、vendor、docs 和 examples 内容，检查包版本一致。
4. 计算 SHA-256 和文件长度，以 Ed25519 签署 manifest；输出 wheel、`feed.json`、`SHA256SUMS`、各文件的 `.sha256`。指定 `--include-source` 还会生成包含测试与发布脚本的源码 `.tar.gz`。
5. 在输出目录先写不可变的版本文件，最后原子替换 `feed.json`；相同版本的不同内容会被拒绝，已有 feed 也不能倒退版本。临时构建目录会在成功或失败后清理。

重复相同源码构建会得到相同的 wheel 和源码包。`feed.json` 的发布时间会随构建变化。`SOURCE_DATE_EPOCH` 固定用于可复现归档，不代表真实发布日期。

## 给其他项目的完整可复制包

在原发布命令追加 `--include-portable`，从已经逐文件验证的 wheel 同时产生 `chaos-intelligence-X.Y.Z-portable.zip`；默认发布方法和 wheel 更新 feed 保持原合同。ZIP 随不可变产物及 SHA256SUMS 发布，签名 feed 仍选择原 wheel。也可对已取得的 wheel 使用 `chaos package build WHEEL --output ZIP`，无需维护者私钥或源码检出；这只做本地转换，不建立远程发布源。

复制方式、原 SDK、依赖、数据隔离及版本目录升级见 [安装与部署](INSTALLATION.md#作为完整包放进其他项目)。完整包源自同一 wheel、同一版本，含原 metadata/许可证/RECORD；不复制开发历史、业务数据、密钥或旧构建，不向宿主旧包目录叠加。

## 上传顺序

把输出目录中的版本 wheel、可选源码／portable 包及校验文件上传到配置的 HTTPS 目录；另行生成的 project ZIP 同样以新版本文件名和校验值交付。确认 wheel URL 可读取，再最后替换 `feed.json`。不要修改已发布版本的 wheel；更改源码后递增版本重新构建。可给 wheel 配置长期缓存，feed 应短缓存或要求重新验证。

首次对外发布还需要实际 HTTPS 托管地址和通过可信渠道分发的公钥；这些不能由一个本地构建命令自动建立。安装者的配置、检查更新和受监督的一键切换参见 [更新安装说明](UPDATES.md)。

## 签名格式

feed 包含 `schemaVersion: 1`、稳定版本、`channel`、`publishedAt`、`apiVersion: "v1"`、`storageVersion: 1`、`installerVersion: 1` 和 `artifacts`。wheel 描述包含 `kind`、`filename`、HTTPS `url`、`sha256` 和 `size`。签名覆盖除 `signature` 字段外的整个对象，以 UTF-8 编码，JSON 对键排序，分隔符为 `,` 和 `:`，不转义非 ASCII 字符。

`signature` 包含 `algorithm: "ed25519"`、公钥原始字节 SHA-256 前 16 个十六进制字符组成的 `keyId`，以及 base64 `value`。密钥 ID 用于识别，实际信任来源是安装时固定的公钥。

## 公开仓库发布前检查

先核对[许可范围与托管平台条款](LICENSING.md)。仓库许可不能覆盖向平台另行授予的训练权利；若“不得训练”是发布条件，确认托管方式满足该条件后再上传。

只提交源码、测试、文档、示例、构建配置和必要的仓库文件；不要直接提交整个开发目录。`.chaos/`、`.development/`、`.env`、运行数据库、私钥、旧制品和包缓存均不属于源码发布快照。`.gitignore` 防止常见误提交，但发布前仍需核对实际暂存文件和密钥扫描结果。

使用干净源码重新构建同一版本的 wheel、project ZIP、portable ZIP 和离线文档，核对 LICENSE、THIRD_PARTY_NOTICES.md 和上游声明随包保留。GitHub 自动生成的源码 ZIP 没有 project ZIP 的目录启动器；README 必须区分这两种安装方式。上传制品时记录版本与 SHA-256，不能把尚未上传的本地包称为线上发布。
