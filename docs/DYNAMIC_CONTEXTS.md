# 原生任务局部上下文

0.72.0 在原 AST 解释器和协作调度器中实现 `ContextVar`、`ContextToken`、`ContextScope` 与 `Context`。名称仅用于诊断，同名变量仍有不同身份；这不是按提示词或变量名识别角色的应用功能。

## 变量、缺省值与恢复

`context_var(name)` 创建没有缺省值的变量，`context_var(name, null)` 明确以 Null 为缺省值。`v.get(fallback)` 依次选择当前绑定、显式 fallback、声明缺省值；全部缺失时抛 `context_lookup_error`。`v.set(value)` 返回 Token；`v.reset(token)` 恢复原绑定或移除新增绑定。

Token 必须属于原变量、原 Context 和活动执行周期，只能 reset 一次。错误 reset 不消费 Token，手动 reset 可以不按创建顺序进行。`token.["var"]` 和 `token.old_value` 是语言级访问；无旧值时 old_value 抛 lookup 错误。旧值在 reset 后仍可读取，执行周期结束才释放。Token 可用同步 with 管理恢复；同一 Token 只能进入一次，活动租约内显式 reset 被拒绝，避免破坏词法恢复。`var` 是现有保留词，成员使用原来的方括号语法。

```chaos
let trace = context_var("trace", "root");
let token = trace.set("request-42");
let saved = copy_context();
trace.reset(token);
[trace.get(), saved.get(trace)]
```

结果为 `["root", "request-42"]`。变量可以保存原生函数、智能体、精确数值、类实例、容器及合法运行时句柄；读写沿用原类型、所有权和权限检查。权限受限的读取继续投影可调用值的权限上限，不能通过上下文带出更宽权限。

## 复制与执行

`context()` 创建空 Context；`copy_context()` 浅复制当前绑定；`context_copy(c)` 和 `c.copy()` 复制指定 Context。绑定表彼此独立，普通值身份保留，可变实例仍有原有共享引用语义。声明缺省值不属于 Context 的显式条目。

`c.get(v, fallback)`、`context_get_in(c,v)`、`c[v]`、`getitem(c,v)` 读取显式条目；它们不会使用变量声明缺省值。`c.has(v)`、`contains(c,v)`、`length(c)`、`c.keys()`、`c.values()`、`c.items()` 和同步迭代可检查条目，items 返回 `(变量, 值)` Tuple 的 List。写入通过 Context.run 内的变量 set 完成，没有可直接修改的宿主 dict。

`context_run(c, function, args=[], kwargs={})` 或 `c.run(...)` 在指定 Context 内调用原语言函数，保留绑定修改，并在所有退出路径恢复调用者上下文。同一 Context 不能递归或并发进入，退出后可再次使用。直接选定的 async 函数会在该 Context 中执行并跨 await 保持绑定；普通调用产生的子任务仍继承独立快照。函数返回的 Task/Generator 不会被隐式消费，直接选定生成器函数也只创建冷生成器。

## 并发、智能体与作用域

普通 Task、Generator 和 AsyncGenerator 在创建时浅复制绑定；重绑定彼此隔离。生成器跨 yield/send/throw/await 保留自己的 Context。TaskGroup 的词法主体、重入 driver、新建异步资源协议调用和退出栈协议共享调用者上下文。生成器上下文管理器保留生成器自己的 Context。

```chaos
let trace = context_var("trace", "root");
async fn worker(role) {
  with context_bind([{variable: trace, value: role}]) as binding {
    await sleep(0);
    trace.get()
  }
}
[await gather([worker("planner"), worker("executor")]), trace.get()]
```

结果为 `[["planner", "executor"], "root"]`。`context_bind` 在安装前验证全部绑定，恢复所选变量的原值与缺失状态，其他变量的修改保留。恢复覆盖 return、break、continue、异常、条件恢复转移、取消和硬退出。ExitStack 在出栈前检查租约所属 Context 与 LIFO 顺序。

智能体工具、Team 和 std/swarm 继续通过原函数调度执行。把 `trace.get()`、业务策略等显式放入智能体输入即可；上下文本身不捕获工具权限、密钥、角色授权、录制权限或活动 handler/restart 栈。所有函数可从 `std/context` 导入。

## 工具链和边界

共享 NativeSpecs 提供类型、参数、只读成员与代码提示。 元数据通过显式 receiver_type 区分新绑定方法；member_specs_for(type) 返回不可变成员目录，spec_for(id) 与 all_specs() 提供全部声明。原 SPECS 兼容调用目录维持历史枚举契约（包括已有 Type、TaskGroup 和退出栈方法），新增成员通过显式 compatibility_export 与完整目录区分，IDE 的成员与链式调用提示由完整目录推导，不执行实例工厂或回调。IDE、无界面 CLI、SDK、HTTP、源码/IR、宏生成和 Program 仍使用同一 AST 语义；分析、格式化、调试预览、JSON 与 str 只输出句柄元数据，不执行回调或泄露绑定、缺省值和 Token 旧值。宏的纯求值阶段不能执行 context 函数；可生成运行时调用。编译器把上下文调用作为有副作用的调用屏障，GPU/JIT 不把它当纯数值核。

每个执行周期最多创建 10000 个上下文原生值，累计浅复制/新增绑定最多 100000 项，每次复制按条目计入步数。复制复杂度为 O(n)，不宣称 Python contextvars 的 O(1) 复制。调度器完成任务、生成器和资源清理后释放绑定、缺省值与 Token 旧值引用；内存回收仍由宿主 Python GC 执行。

这借鉴 Python contextvars 的任务绑定，但生成器隔离和直接 async Context.run 是本语言扩展，不是字面兼容 Python API。`class_of` 仍用于 Class/Instance；上下文值使用原生类型契约和 `type_of`。本能力不等同完整 Python/Lisp 生态兼容、自托管编译器或独立 GC 已完成。

参考：[Python contextvars](https://docs.python.org/3/library/contextvars.html)、[PEP 567](https://peps.python.org/pep-0567/)。完整示例：[dynamic-contexts.chaos](../examples/dynamic-contexts.chaos)。


## Context.run 的执行状态

Context.run 在[真实资源协议帧](EXECUTION_FRAMES.md#资源协议与动态上下文的执行状态)中保留目标 Context、回调参数、上一上下文及完整结果。进入、暂停、恢复与所有退出路径使用相同活动身份；退出后恢复原调用者上下文，仍拒绝同一 Context 的递归或并发进入。直接选定 async 回调与返回冷 Task／Generator 的区别沿用本页合同。上下文状态不包含额外模型权限，也不等同可复制 continuation 或跨执行周期的活栈迁移。
