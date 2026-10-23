# 源码断点与交互式调试

调试器暂停真实解释器，检查当前调用栈和局部值。它使用运行开始时固化的项目版本；编辑源码会生成新版本，不能改变已经暂停的运行。暂停期间 HTTP 服务、编辑和其它项目仍可使用。

## 网页工作流

在“Chaos 源码”中保存项目，配置断点后启动调试。暂停面板显示原因、源码位置、调用栈与局部值，提供继续和单步命令；停止沿用运行取消。继续操作绑定 `pauseId`，过期页面上的命令不能意外放行下一次暂停。

断点位置写成项目内文件路径和一基行号。调试器观察源程序执行，不在观察期间调用用户函数或运行任意调试表达式。函数、Task、Generator 等特殊值显示摘要。

## 无界面接口

启动运行时附加 `debug` 配置。`GET /api/v1/runs/{runId}/debugger` 读取状态，`POST` 同路径发送调试命令。Python SDK 与 CLI 使用同一运行服务；精确签名和命令见 [SDK](SDK.md) 与 `chaos debug --help`。

调试状态与持久化运行状态分开：运行仍可能是 running，而调试器是 paused。暂停有有限等待期限；取消与服务关闭必须唤醒暂停线程。暂停时长不消耗程序计算时间，但不能阻止已经发出的外部请求或撤销已产生的副作用。

服务重启后旧运行按中断记录处理，不能还原 Python 线程中的暂停栈。本版没有任意表达式求值控制台、原生代码调试或远程进程附加。

## 情况暂停与恢复出口

启动调试时可以勾选“未处理情况时暂停”，对应 `pauseOnCondition`，默认关闭。语言也可显式调用 `invoke_debugger(condition(...))`。暂停原因是 `condition`，恢复区域显示结构化情况和当前真实可见的出口；选择一个出口，填入参数 JSON 数组和具名参数 JSON 对象，再点击恢复。参数没有表达式求值，不从错误文字或模型回复猜测出口。参数输入复用编辑器的精确 JSON 编解码器，保留任意精度整数和 `2.0` 等数值的类型；超出有限范围的浮点数会在发送前拒绝。

状态中的 `conditionRecovery` 为纯数据：`condition` 包含 kind／message／data，`restarts` 包含 id／name／report／mode／active，另有 total／truncated。该快照只属于当前 pauseId；运行继续、取消或切换暂停后不能再次使用。HTTP 查询不执行 test 或恢复函数。

命令 `restart` 要求同一次暂停的 `pauseId`、已发布的精确 `restartId`、`args` List 和 `kwargs` Record。非法、非有限或超限 JSON 在唤醒前拒绝；旧 pauseId 冲突，输入草稿保留。恢复最终在解释器线程重新检查所有权、动态范围和取消，再执行原参数绑定及展开路径。选择后仍可能出现真实参数契约或恢复函数错误。

继续／单步在这种暂停中表示拒绝恢复，原情况错误随后传播。inline 选择可以正常返回恢复值，case 选择先展开清理再执行出口；已完成的外部动作不会重试或回滚。暂停仍有原有限期限，服务重启不能恢复活栈。详见[语言情况合同](CONDITIONS.md)。

```sh
chaos debug start PROJECT --version VERSION --no-entry --pause-on-condition
chaos debug inspect RUN
chaos debug restart RUN --pause-id PAUSE --restart-id RESTART --args-json '[41]' --kwargs-json '{}'
```

## 命令行示例

先用 `chaos serve --port 8891` 启动服务，并在该服务保存项目。下面的 PROJECT、VERSION、RUN 和 PAUSE_ID 分别替换为真实项目 ID、保存版本、启动输出中的运行 ID 和 inspect 输出中的当前暂停编号：

```sh
chaos debug start PROJECT --version VERSION --server http://127.0.0.1:8891 --breakpoint main.chaos:5
chaos debug inspect RUN --server http://127.0.0.1:8891
chaos debug step_into RUN --pause-id PAUSE_ID --server http://127.0.0.1:8891
chaos debug shell RUN --server http://127.0.0.1:8891
```

交互 shell 支持 c 继续、i 进入、n 跨过、o 跳出、p 请求暂停、s 栈、q 取消，以及 `break FILE:LINE`、`clear`。EOF 取消该运行。非交互控制还包括 `continue`、`pause`、`step_over`、`step_out`、`breakpoints`、`cancel`。

默认在入口暂停，`--no-entry` 改为等待断点。默认暂停等待 300 秒，`--pause-timeout` 允许 0.1 至 3600 秒；超时终止运行并记录错误，避免永远占据运行槽。断点上限 100 个，空白行不保证有可暂停的 AST 节点。单步以源码行与逻辑调用栈为单位；Task 栈同时显示 taskId，宏展开节点沿用调用位置及展开来源。


异步流消费在真实 Task 中恢复生成器，await/yield 的源码检查点仍可暂停；局部变量中的 AsyncGenerator 显示有界状态摘要，不展开其内部执行栈或调用属性。恢复操作的任务身份与流句柄身份不同，调试器显示当前执行的逻辑调用栈。

断点绑定固定快照内的文件与行号，不随重命名或格式化自动迁移。开启保存时格式化时，应先保存，再按规范源码设置断点。外部效果进行期间的暂停请求会在下一个 AST 检查点生效。

## 对象与绑定方法

在方法中暂停可查看 `self`、成员状态与调用帧。调试摘要只读取解释器保存的受限数据，遵守层数、条目和脱敏限制；不会为显示值调用用户方法、`__call__` 或 Python 属性。类型/实例摘要不是可执行对象或可恢复的跨进程快照。

## 装饰器和属性调试

装饰器与 getter/setter 使用普通调用栈，可以在其函数体设置断点、单步并查看参数。classmethod 的接收者是 Class，普通方法的接收者是 Instance。局部变量面板仅显示内置描述符的惰性摘要，不执行 property getter，不会为了显示字段产生 Agent/工具调用。

异步属性等待继续显示当前 Task 标识；界面暂停不会把在途的外部效果冻结。

用户 `__get__`、`__set__`、`__delete__`、`__set_name__` 与 property deleter 都可以在其函数体设置断点并查看当前任务栈。调试预览仅检查原始字段和协议名称，绝不触发访问器或命名通知；因此展开变量不会造成隐藏的 Agent 调用。

运算协议方法可以设置断点并查看所在任务的调用栈。NotImplemented 显示为惰性哨兵描述；对象预览只查看原始字段，不运行 `__eq__`、`__bool__`、`__len__` 等方法。调试展开不会产生隐式 Agent 调用。

可在用户 `__iter__`、`__next__`、`__anext__` 及关闭方法中设置断点。迭代句柄预览仅显示身份、状态、忙锁等有界元数据，不展开被包装的目标，也不执行方法。异步推进的任务与生成器调试使用同一调用栈机制。

可以在 __getitem__、__setitem__、__delitem__ 和它们的描述符中断点。Slice 预览仅包含三个有界标量字段；当前任务、Agent 效果、取消机制和其它方法相同。旧语法版本属于运行快照，不由调试器根据字段名猜测。

## 暂停点的任务快照

调试器在解释器线程到达真实暂停点时保存有界任务列表，显示 task/gather/scope、身份、状态、父任务、组归属与成员，以及同一暂停点的 TaskGroup 卡片。选择调用帧可以关联其 taskId；其它任务只显示元数据，不执行 getter，也不伪造它们的局部栈。恢复后这个列表不应冒充实时全线程状态；下一暂停点会更新。详见[任务状态](TASKS.md)。

## 通道等待状态

Channel 列表来自暂停时的调度器快照，最多100条；显示容量、缓冲与等待数量，以及最多100个发送／接收 Task ID。Task 的 channelId 来自原生操作，不凭函数名匹配；它与当前运行上下文共同标识通道。快照不包含消息值，局部变量中的 Channel 仅有有界描述；恢复执行后不保留伪实时卡片。见[通道状态](CHANNELS.md)。

### 原生等待任务

暂停快照中的 sleep／wait 任务沿用任务身份和成员 ID，并附 timing：seconds、until、timeoutSeconds、remainingSeconds，可空字段用 null。计时采用单调经过时间，暂停不会推进语言任务，恢复后处理到期操作；快照不是网页计时器，恢复后清除旧显示。详情见[等待指南](WAITS.md)。

## 原子选择状态

TaskInfo.selection 为 null 或含 caseCount、cases、casesTruncated、index 的有界记录。cases 最多100项，只含候选序号、收发方向与真实通道 ID；不暴露消息，也不伪造分支 Task。选择沿用 timing 的 timeoutSeconds／remainingSeconds，候选值局部预览只显示安全描述。通道等待数按候选注册计数，重复 Task ID 表示同一选择的多个候选。详细语义见[选择规范](SELECT.md)。

## 取消保护快照

0.49 的调度快照新增有界 shields 列表、shieldTotal 与 shieldsTruncated，每项与 cancel_shield_info 的安全记录同源。Task 卡片同时显示 shieldDepth／cancellationDeferred，保护范围卡片显示所属 Task、深度、状态和待交付请求。旧快照缺少这些字段时仍可展示；安全预览不执行用户 getter。详见[取消保护](CANCELLATION.md)。

## 元组快照

调试器将原生 Tuple 显示为带有元素列表的 Tuple 快照；每层及总节点使用既有有界预算，敏感文本沿用脱敏。预览不会执行元素、用户属性或迭代器，截断信息不代表元素丢失。

## 完成分支定位

loop_else 和 try_else 保留其被包装循环／try 的原始定位和检查点，otherwise 块中的语句可设断点并单步。暂停值只显示实际词法环境，已经离开的迭代目标和 catch 绑定不会泄漏到成功分支。取消、继续和异步挂起沿用原调度器；见[完成语义](CONTROL_COMPLETION.md)。

## 异常分支定位

模式 catch 的 guard 与 handler 使用本候选局部绑定；断点不会看到被拒绝的兄弟候选名字。裸 throw 保留原错误首次位置。活动错误属于运行时私有状态，不作为用户变量出现在局部值列表中。见[异常规则](EXCEPTIONS.md)。

0.71.1 在调用帧视图退出时取消并清理共享代码渲染状态。调试结束后代码区保持空白；已解析但迟到的高亮响应不能恢复旧代码或修改下一次暂停快照。这不改变断点、单步或实际运行源码。


## 0.77 生成函数的原生源码调试

function_from 在验证 Code、显式绑定和语法之后、执行默认值之前，将规范源码登记到原运行时。code_from／code_edit／查询不会登记。注册表每个 Evaluator 和 DebugSession 最多64个唯一源、1000000字符，不驱逐活动函数的源；同标识冲突和覆盖固定项目文件会在默认值前报错。注册表只保存不可执行文本／语言版本／哈希，在 Evaluator 回收时释放，合法旧函数及嵌套函数跨同一 Evaluator 轮次仍保持来源。

调试器快照新增 generatedSources，按 code_ir(c).file 给出 source、languageVersion、sourceHash、semanticHash 副本，沿用秘密脱敏。虚拟文件名是带语言版本的内部不透明标识，不要解析其命名规则。初始 start_debug 仍只接受固定项目文件；函数构造登记后，原 debug_command 的 breakpoints 命令可以设置虚拟源断点。普通同步、async、生成器和 native-flow 调试沿原 AST／当前逻辑调用栈运行，暂停时间、取消和权限规则不变，不重放默认值或工具。

界面显示只读生成源码，使用自身语言版本和独立分析入口；项目 files、已保存 run.snapshot、IR 和编辑草稿不会因此改变。脱敏文本的字宽可能变化，显示列偏移不应视为未经脱敏原文的精确字符位置。API v1 和 SDK 调试命令不变。生成源码调试不提供跨异步／资源活栈持久化、自举、通用 AOT 或独立 GC；同步可组合 continuation 的独立合同见[Continuation](CONTINUATIONS.md)。


## 0.78 原调度器中的模块构造

`module_from(program, bindings={})` 对原生 Program 的封闭包创建独立模块实例，在当前 Evaluator、逻辑任务和效果权限内执行原 AST，返回与 import 一致的导出 Record。显式 bindings 浅复制名称关联、保留活函数／类／智能体的身份；不隐式捕获调用者局部变量。每次构造有独立词法单元和依赖缓存，同实例的菱形导入仅初始化一次，标准模块也不借用外层缓存。宏在原 ProgramBundle 中纯展开，运行时不会导出宏。顶层 await、四类函数和原 AgentTool／Team／群体回调可组合。

代码不是宿主 eval，也不是 run_program 的 JSON 后台任务。构造失败不返回完整 namespace，不自动重试，更不回滚已完成的外部效果。模块返回值中的冷 Task／Generator 不被额外启动。模块及导出函数中的 current_program 返回该封闭定义且 origin 为 null；项目采用和持久演进仍需原 Program／Process 合同。相同定义的 data 标签按源码身份匹配，Class、词法状态和缓存按实例独立。

原生成源码表继续保留四字段。模块全图先通过原64源／1000000字符配额的批量预检，再执行初始化与默认值；不能覆盖固定项目文件。额外 generatedSourceContexts 是纯逻辑文件／虚拟文件映射和版本数据，IDE 在只读源视图中分析完整自有文件包，标准库由原解析器解析，不写项目、快照或编辑草稿。受信自定义 source_handler 的外部副作用不承诺回滚。

这不代表完整外语兼容、reader 宏、通用跨边界 continuation、活栈迁移、自举、AOT 或自研 GC 已完成。

情况暂停沿原执行游标上的[情况恢复帧](EXECUTION_FRAMES.md#情况与恢复的执行状态)进行。出口发现和选择保留原逻辑 holder 与能力上限，暂停深度和检查点在异常／取消时恢复；HTTP／SDK／CLI 沿原命令合同恢复同一次暂停。帧字段属于真实待决计算，调试快照仍是有界纯数据，不能用于重建活栈或重新进入已经退出的恢复范围。

模块运行状态现在由[六种模块算法帧](EXECUTION_FRAMES.md#模块初始化导入与导出入口的执行状态)推进。封闭实例的原模块调试 frame、局部环境、虚拟源位置和 taskId 保留；普通项目仍按原语句分别进入 evaluate／resolve Runner。内部帧不是可由调试 JSON 重建或跨进程恢复的暂停栈。
