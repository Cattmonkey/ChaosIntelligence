# 程序历史：可复现的自编程与恢复

0.26 把每次成功保存的完整程序作为不可变版本保留。dot 可以用语言读取历史源码、比较行为、测试旧实现，再通过原有采用操作恢复。版本历史属于同一 Store／Context，既不另建机器人引擎，也不依赖网页或 Codex App。

## 历史是程序数据

`program_history(after=0,limit=50)` 返回冷 Task；await 后读取当前项目的元数据页。`program_revision(version)` 返回冷 Task；await 后得到当前解释器拥有的 Program。构造任务不读取数据库：

```chaos example=program-history-cold
[type_of(program_history()),type_of(program_revision(1))]
```

结果为 `["Task","Task"]`。可像其他函数一样组合：

```chaos example=program-history-function-composition
map(type_of,map(program_revision,[1,2,3]))
```

结果为三个 `"Task"`，没有查询或执行历史程序。`await` 需要项目宿主；纯 `Framework.evaluate` 可以构造任务，但不能查询持久历史。

历史页严格包含 `{items,nextCursor,currentVersion,firstAvailableVersion}`。items 按版本升序排列；after 是排他的版本游标，limit 为 1–100。每项有 `{version,definitionHash,entry,created,kind,runId,reason}`，不含完整源码。created 是记录建立的 Unix 时间；kind 为 created、saved、patched、adopted 或 migration。查询的头版本、覆盖起点和当前页属于同一个数据库读快照；下一页查询时可能已有新提交，可按需要使用第一次取得的 currentVersion 作为上界。

`program_revision` 只查询当前项目的精确版本，不接受项目 ID 或提示词；找不到就报错，不偷偷返回最近版本。它保留入口、全部模块原文和逐文件语言版本，也保留历史来源 `{projectId,version,definitionHash}`。`program_info`、`program_ir`、`program_edit`、普通调用及 `program_probe` 可继续处理这个值。JSON 描述不自动变为活 Program，更不会获得新权限。

历史读取是显式环境效果，遵循共享预算、取消与项目边界。它不运行所读程序，也不消耗子程序执行的嵌套深度或 64 次执行计数。纯 probe 不能查询历史；Skill 录制期间也拒绝这些效果。较大程序通过 Program 专用有界通道取回，不把普通 JSON 输出的 250000 字符上限放宽。完整程序仍受既有模块数和源码总量限制。

## 恢复是一次经过测试的新提交

```chaos
let baseline=current_program();
let historical=await program_revision(input.version);
let cases=input.cases;
let report=await program_probe(historical,cases);
if !report.passed {throw report} else {null};
await program_adopt(baseline,historical,cases,reason="Historical implementation passed current acceptance cases")
```

假设当前是 v8、选择 v3，成功采用会生成 v9，其源码与 v3 相同。v3 和 v8 都继续可读。并非把版本号减回 3，也不会删除中间历史。采用仍需要当前运行的未修改 Program 作为 base、当前项目的 program.adopt 授权、真实纯测试，以及版本／源码哈希 CAS。历史 Program 的 origin 只是来源身份，不能冒充当前基准。

采用前服务器会重新执行给定测试；不能用外部传入的“passed”报告跳过。并发修改、测试失败、无权限或取消会阻止尚未提交的采用。当前词法函数和运行栈保持原版本，下一次 Run 或下一轮 Process 才使用新源码。

恢复范围只有 Program 的定义。它不会恢复旧项目权限、名称、模型配置、Process 状态、会话消息、工具副作用或浏览器句柄。程序提交和 Process 检查点仍是不同事务；提交后中断应根据持久回执显式协调，不能重播工具动作来猜测结果。

## 所有编辑方式使用同一份历史

项目创建、源码／文件／IR 保存、图形编辑、平台工作流 patch 和 program_adopt 都在当前 Store 写事务里追加程序快照。历史插入失败时，项目修改和同事务事件一起回滚；版本冲突和验证失败不生成虚假修订。

每个项目版本都会有一条记录，即使这次只改名称、能力或图形坐标，源码哈希可能与上一版相同。这些记录只保存程序定义和有限来源元数据，不保存旧权限或配置。注释、模块入口和逐文件方言保持原样；图形坐标不是 Program 定义的一部分。

升级旧安装时，只记录升级当时仍存在的当前程序，kind 为 migration。firstAvailableVersion 披露覆盖起点；更早的已丢源码不会根据事件、运行记录或当前文本伪造出来。迁移不增加已有项目版本、不改其源码、不添加假保存事件。历史保存在同一个项目数据库，按现有备份方式保留；目前没有自动清理策略，长期运行需要为累计源码历史预留存储。

## 同源 SDK、HTTP、CLI 与 IDE

Framework／Client 新增：

```python
history = app.program_history(project_id, after=0, limit=50)
revision = app.program_revision(project_id, version)
```

SDK 的 revision 为 `{definition,origin,metadata}`；metadata 与历史页同项一致。它是 JSON，不自动执行。读取历史没有新写权限，也不会产生保存事件。恢复复用 `adopt_program(project_id,current_version,revision["definition"],expected_hash=current_hash,cases=cases)`。

HTTP 新增两个只读路径：

- GET `/api/v1/projects/{id}/program-history?after=0&limit=50`
- GET `/api/v1/projects/{id}/program-revisions/{version}`

原 program-snapshot、program-adopt、program 和源码保存接口不变。CLI 连接同一服务：

```sh
chaos program history my_dot --server http://127.0.0.1:8765
chaos program revision my_dot 3 --server http://127.0.0.1:8765
chaos program snapshot my_dot --server http://127.0.0.1:8765
```

IDE 的“程序候选预览”复用这些接口浏览历史，显示版本、哈希和来源，并与当前候选基准逐文件比较。选择历史不会直接覆盖源码。明确载入后，它成为候选，采用基准仍是当前已保存版本；测试通过后按原流程提交。未保存源码、候选草稿、迟到请求和远端更新继续遵守已有冲突保护。历史浏览不能把未保存草稿变成已保存历史。

## 能发现回归并恢复自己的 dot

随包提供 [启动器](../examples/recovering-dot/main.chaos)、[持续函数](../examples/recovering-dot/dot.chaos)、[行为模块](../examples/recovering-dot/behavior.chaos)及[纯测试入口](../examples/recovering-dot/tests.chaos)。项目需要 process.manage、session.manage、program.adopt，并配置本系统 native 模型。启动示例：

```python
from pathlib import Path
import time
from chaos import Framework
folder=Path("examples/recovering-dot")
files={p.name:p.read_text() for p in folder.glob("*.chaos")}
with Framework("dot.sqlite3") as app:
    app.create_project("dot",source=files["main.chaos"],files=files,
        capabilities=["process.manage","session.manage","program.adopt"])
    app.start("dot",1,None,mode="source").result()
    deadline=time.monotonic()+120
    while app.get_process("dot","dot")["status"] not in ("completed","needs_reconciliation","stopped"):
        if time.monotonic()>deadline: raise TimeoutError("Inspect the running Process")
        time.sleep(.1)
    print(app.get_process("dot","dot")["status"])
```

持续函数使用普通 AgentSession 提出优化定义。程序自己验证修改只限行为模块，实际运行初始纯测试，再采用候选。下一轮运行更完整的验收；若失败，则从持久历史取回基线、重新验收并采用为新版本；第三轮验证恢复后的实际结果。停止条件与验收案例均为普通语言代码／显式输入，不靠关键词识别完成状态。

确定性回归测试通过真实 OpenAI SDK 连接本地提供方：原函数计算平方，候选 value*2 恰好通过 input=2，却在 input=3 时返回6；dot 从历史取回 value*value，形成新版本，下一轮返回9。测试在候选采用后关闭并重开宿主，确认旧程序、角色会话和持续状态确实来自数据库。正确候选则直接通过验收，不强制恢复。

这是恢复机制与有限测试边界的演示，不是模型优化质量或任意候选正确性的证明。自动恢复策略需要程序事先定义；它不能撤销外部效果，也不提供解释器自举或任意活调用栈迁移。
