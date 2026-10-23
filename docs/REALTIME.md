# 实时交互与界面／无界面开发

工作流创建、图修改和平台补丁在服务端统一生成可读源码，并在原事务提交同一份 `source`、入口文件与 IR；SDK、HTTP、网页及文件工作区无需另做格式化保存。仅节点坐标改变时保留已存源码、注释及定义哈希，不在读取旧项目时重新排版。

服务端项目版本是共同提交点。源码、模块、结构 IR、关系画布和平台关系补丁在同一事务中保存；运行冻结该版本。界面保留独立草稿，网络返回必须通过版本检查，不能用旧响应覆盖新版本。未保存的输入不会被当成已执行代码。

首次使用实时功能可直接从[交互教程](#从运行到追加指导)开始；对接其他程序见[事件订阅](#从其他程序订阅实时事件)。文件与网页的一致性规则如下。

## 从本地文件编程

已有目录项目时，在目录中运行 `python3 chaos.py` 即启动同目录 IDE，原 Workspace 协议在后台周期同步已保存的文件。独立 `python3 chaos.py run` 读取同目录源码和配置，默认使用内存数据库；`--persistent` 使用独立的 CLI 数据库，仍不与 IDE 共享运行历史。无需另外 checkout 或启动第二个 watcher。首次使用和移动目录见[快速开始](GETTING_STARTED.md)。

下面是连接一个已运行远程／独立服务的另一种方式。先运行服务，再在另一终端检出项目：
```sh
python3 -m chaos serve
python3 -m chaos workspace checkout ./my-program --server http://127.0.0.1:8765 --project workspace
python3 -m chaos workspace sync ./my-program --watch
```
用任意编辑器修改并保存目录中的 .chaos 文件。watch 默认每 0.5 秒检查一次：有效本地修改经版本 CAS 提交，网页通过 SSE 接收；网页提交的修改会拉回本地。编辑中的语法错误会报告一次并继续等待修正，暂时断网会重试。

```sh
python3 -m chaos workspace status ./my-program
python3 -m chaos workspace run ./my-program --input-json '42'
```

独立的 `chaos run file.chaos` 仍是离线执行入口，不会自动连接或修改网页项目。需要联动时使用 workspace 命令或 Client；不要同时打开一个正在被服务使用的数据库运行第二个 Framework。

## SDK 与观察接口

```python
from chaos import Client, Workspace
client = Client("http://127.0.0.1:8765")
snapshot = client.snapshot("workspace")
p = snapshot["project"]
p["source"] = "input + 1"
p = client.save_project(p, from_view="source")
run = client.start(p["id"], p["version"], 41, trace=True)
assert run.result() == 42
workspace = Workspace.checkout(client, "workspace", "./checkout")
workspace.sync()
```

GET /api/v1/projects/{id}/snapshot 在同一 SQLite 读事务返回 project、eventCursor、runs。从 eventCursor 订阅 SSE 可接续快照后的事件；连接恢复依靠事件游标补齐。网页默认跟踪最新外部运行，历史运行补读持久事件。

trace=True 产生最多 200 条 program.step 和一条截断说明，包含版本、文件、行列、结构类型与来源。网页和 workspace run 开启它；普通 Client.start 默认关闭。它是有界观察，不是完整调试器或每一步动画承诺。内嵌图函数使用图节点定位，模块源程序可定位语法树。

## 执行中的动态交互

语言运行、后台任务和编程助手使用同一浏览器实时订阅器，分别连接各自的持久事件库。界面处理事件后才推进游标；断线后从已处理位置补读，重复事件去重，旧连接的迟到消息不能更新另一个项目或对话。服务拥有的任务不会因为关闭页面或读取失败而取消；宿主服务必须保持运行，停止仍是显式请求。

- **语言运行**：执行事件持续追加。向上滚动阅读时保持位置，有新事件会出现“跟随最新”按钮；点击后恢复跟随。列表最多展示最近250条，持久记录可从接口分页读取。
- **公开进度**：程序调用 `await progress_emit(kind, data)` 时，“运行结果”同步显示当前公开进度及数据。它是执行中的预览；只有运行终态的 output 才是最终结果。`kind: "result"` 本身不会结束运行。
- **编程助手**：对话中显示“执行过程”，可展开最近40条已接收的公开阶段。断线时保留原任务、取消入口和未发送输入，恢复后继续观察；无需再次提交同一个任务。阅读提案时保持展开状态和滚动位置。
- **后台任务**：活动卡片由持久事件更新；任务快照在事件到达后合并刷新，另有周期性状态校对。快照请求失败不会清空最后已知状态。

先用[自动返回进度的完整程序](../examples/live-progress.chaos)观察三个平方的计算过程，再按下节学习运行中追加指导。两个示例均无需模型 Key。它们通过 `progress_emit` 发布公开数据，实际程序仍由同一语言解释器执行。

这些流包含程序明确发布的数据、执行阶段、工具动作与结果，不是模型内部推理或逐 token 文本。运行流中的 `ui.delta` 继续交给原有页面／报告渲染器；无需建立另一套生成页面状态。

## 从运行到追加指导

前置条件：已按[快速开始](GETTING_STARTED.md)打开一个可运行的目录项目。先在测试项目练习，保留自己的业务源码。

### 网页中亲手操作

1. 打开[交互进度示例](../examples/interactive-progress.chaos)，将完整代码复制到测试项目的入口文件并保存。默认入口是 `main.chaos`；仅在文件列表选择示例文件不会切换入口。
2. 从“运行 → 输入与调试设置”把输入设置为 JSON `null`，勾选“将输入作为 JSON 解析”，然后点击“运行”。
3. 在底部“执行事件”或“运行结果”看到计算进度，随后出现“请从运行面板追加指导”。程序此时最多等待30秒；保持服务运行。
4. 从命令面板选择“运行：追加当前运行指导”，输入 `先验收，再继续` 并发送。选择当前运行的默认指导目标，无需新建机器人或工具权限。
5. 先看到“已入队”，程序消费后显示“运行已接收”。运行结束时，最终结果为：

```json
{"received":true,"guidance":{"text":"先验收，再继续"},"squares":[1,4,9]}
```

未发送指导时，等待结束后返回 `{"received":false,"guidance":null,"squares":[1,4,9]}`。示例不会把自然语言当作源码执行，也不会猜测关键词；它把收到的数据原样放进结果。需要按自然语言调整行为时，把该数据明确交给 Agent／规划函数，参见[模型智能体示例](MODEL_EXAMPLES.md)和[原生 Bot](NATIVE_BOTS.md)。[指导控制器模块](../examples/guided-bot.chaos)导出可组合函数，需要宿主程序导入并调用，不是这个入门程序的替代入口。

如果先出现超时错误，检查整个运行的时间预算是否小于等待时间；等待不会延长共享预算。无需等待的验证方式是把运行输入设为 JSON `{"waitSeconds":0}`。收到 `received` 只证明消息进入程序，不能据此认定用户要求已被理解、采用或完成；本例最终值只证明计算与回传。

### 在同目录终端使用

把下载的文件保存为工程根目录的 `interactive-progress.chaos`，在该目录执行：

```sh
python3 chaos.py check interactive-progress.chaos
python3 chaos.py run interactive-progress.chaos --input-json '{"waitSeconds":0}' --stream
```

`--stream` 持续输出 JSON Lines：每行是独立 JSON，公开进度事件的 `type` 为 `bot.progress`，内容在 `payload.kind` 和 `payload.data` 中。成功结束后追加 `type: "run.result"` 的包装记录，其 `output` 是最终值；它不是带持久事件 ID 的数据库事件。程序失败时返回非零退出码并向 stderr 写错误，不能把之前的进度当作成功。普通 `run` 不加 `--stream` 时仍只输出最终 JSON。

macOS／Linux 的真实交互终端还可以直接追加自然语言：

```sh
python3 chaos.py run interactive-progress.chaos --interactive
```

在 `guide>` 输入 `先验收，再继续` 并回车，得到与网页相同的指导数据。`/help` 查看控制，`/cancel`、Ctrl-C 或 Ctrl-D 取消并清理这个本地命令拥有的运行。`--interactive` 需要 POSIX 系统和真实 stdin／stdout 终端，不能用于输出重定向、管道或从标准输入读取源码。Windows 使用 `py -3` 运行普通命令或 `--stream`，指导通过网页／SDK／HTTP发送；上述 JSON 引号示例采用 POSIX shell。

目录终端自己启动的 `run` 使用自己的运行历史，不会自动出现在另一个 IDE 服务的运行面板。要从多个客户端观察或指导同一运行，连接同一个服务并使用它返回的 runId；不要为了获得流而再次提交原任务。完整 CLI 终端生命周期见[SDK 的实时事件与运行中指导](SDK.md#实时事件与运行中指导)。

### 观察、编辑、指导和停止的区别

| 操作 | 实际影响 |
| --- | --- |
| 保存正在编辑的源码 | 创建新的项目版本；已启动运行继续使用自己的冻结版本 |
| 向当前运行追加指导 | 消息进入当前指导队列；由程序在 `guidance_receive` 等接收边界处理 |
| 向 IDE 编程助手发送需求 | 创建代码提案任务；需要检查并显式“应用并保存”才写入项目 |
| 关闭网页或断开 SSE | 停止当前观察连接；只要宿主服务仍运行，服务拥有的任务继续执行 |
| 退出本地 `--interactive` 命令 | 取消并清理该命令自己拥有的运行 |
| 点击停止／请求取消 | 协作式停止；已发生的文件写入或外部效果不回滚 |

同一页面暂时断线时，源码与未发送输入留在当前页面；恢复连接后补齐事件。刷新、关闭页面或重启浏览器之前，仍需保存源码或使用草稿备份，不承诺恢复未发送的对话文本。查看旧事件时可以向上滚动暂停跟随，点击“跟随最新”回到底部；有限的界面历史与持久事件库不是同一个保留范围。

## 从其他程序订阅实时事件

以下为正式 `/api/v1` 接口，响应类型为 `text/event-stream`。每条 SSE 消息的 `id` 是持久事件 ID，`data` 是 JSON。原来的 JSON 分页接口保持不变。

| 对象 | SSE 路径 | 游标范围 |
| --- | --- | --- |
| 语言运行／持续 Process | `/api/v1/events/stream?projectId=PROJECT`，可加 `runId` 或 `processKey` | 运行事件库 |
| 后台任务 | `/api/v1/tasks/events/stream`，可加 `taskId=TASK` | 后台任务事件库 |
| IDE 助手某一轮 | `/api/v1/projects/PROJECT/assistant/JOB/events/stream` | IDE 助手事件库，并校验项目归属 |

分页读取分别使用 `/api/v1/events`、`/api/v1/tasks/events` 和 `/api/v1/projects/PROJECT/assistant/JOB/events`。运行事件页返回 `nextCursor`，任务／助手事件页返回 `lastEventId`；按各自接口继续读取，不能混用字段或游标。旧 `/api/events` 是网页兼容 SSE 路径，不等价于 `/api/v1/events` 的 JSON 响应。

用实际服务地址、项目或任务 ID 替换占位符，例如观察所有后台任务：

```sh
curl -N 'http://127.0.0.1:8765/api/v1/tasks/events/stream?after=0'
```

上面的 `curl -N` 只观察一次连接，不会自动重连。连接约25秒轮换，持续订阅的客户端需要重新连接。重连时提交 `Last-Event-ID` 头，或 URL 的 `after` 参数；两者同时存在时优先使用前者。游标应保存为原始整数文本：浏览器使用 SSE 的 `message.lastEventId`，避免先经普通 `JSON.parse` 舍入 `data.id` 再转字符串；不同事件库的游标不能混用。指定任务的流在终态尾部排空后关闭；不能只因状态快照先返回终态就丢弃尚未消费的尾事件。订阅所有任务的流持续观察到本次轮换。SSE 注释是心跳，不是任务事件。原生浏览器 EventSource 的自动续传记录的是接收游标；如果处理回调包含异步工作，应像工作台订阅器一样串行处理并自行管理确认游标，不能把已收到但未处理的事件算作已完成。

自建客户端应以[公开 OpenAPI](HTTP_API.md)为稳定接口，不依赖工作台内部全局状态。先取得真实项目或任务 ID，再订阅；`runId` 与 `processKey` 互斥，助手流校验 job 所属项目，非法选择器在流响应头发送之前返回 JSON 错误。

外部客户端应在处理完成后保存游标，并按事件 ID 去重；这不保证业务副作用恰好一次。关闭订阅只停止观察，不自动停止、重发或恢复任务。服务检查周期约0.2秒，浏览器按实际收到的事件更新，不承诺硬实时延迟；反向代理也需允许事件流持续传输。

## 冲突与恢复

两端都发生修改时不自动覆盖或猜测合并。界面显示基准版本与远端版本，保留当前草稿，允许直接导出；“备份草稿并载入远端”先将含未应用控件的草稿保存在本机，再采用远端。可导出备份并重新导入恢复。非法草稿仍可备份，不会偷偷执行旧值。

文件工作区的冲突保存在 .chaos-conflict.json；原基线是 .chaos-workspace.json。watch 遇版本冲突会停止，需要手工合并后重新检出或以当前版本通过 SDK 提交。检出拒绝非空目录。拉取前保存 .chaos-before-pull.json，原文件移动到 .chaos-replaced-* 恢复目录；使用排他文件安装，避免覆盖编辑器在替换窗口新建的文件。外部编辑器已经打开旧 inode 时，其后续写入可能进入恢复文件，应依据冲突记录取回；不会宣称能同步编辑器尚未保存的内存缓冲区。

模块支持子目录和普通隐藏目录；.git、.chaos、.chaos-replaced-* 为保留目录。同步锁协调同一目录的 CLI/SDK 实例。文件拉取按文件提交，不是整个目录的文件系统事务；中断后保留恢复证据并拒绝静默覆盖。

## 数值、快照与实时边界

浏览器所有项目、图、IR、事件、导入导出使用保留数字文本的 JSON 编解码，普通用户记录不会因保留键被误判。HTTP inputJson 可提交原始 JSON（与 input 互斥），运行和输出事件附有 inputJson/outputJson 镜像；大整数、浮点类型与负零保持一致。

执行状态只叠加到相同版本且无草稿的编辑图，或明确的只读执行快照。旧运行不会装饰新代码。SSE 检查周期约 0.2 秒，文件 watch 默认 0.5 秒；这是提交后的持续同步，不是硬实时延迟保证。断线期间保留草稿，恢复后补齐事件并重新核对版本。

## 模式库编写

Plan、Goal、Skill 现在支持统一验证、结构表单编辑、CAS 修订保存、固定版本加载和普通语言模块导出。界面保留与外部更新冲突的草稿；录制回执与新编写定义保持独立。详见[模式库编写与版本一致性](MODE_AUTHORING.md)。

## 历史版本与当前版本

源码、文件、IR、图形及智能体采用在同一写事务追加不可变程序历史，SDK与IDE读取相同哈希。历史程序恢复产生新版本，SSE沿用原保存事件；进行中的Run仍显示自己的版本。历史选择和候选草稿不改变当前源码；远端改变基准时必须按已有CAS冲突处理。见[程序历史](PROGRAM_HISTORY.md)。
