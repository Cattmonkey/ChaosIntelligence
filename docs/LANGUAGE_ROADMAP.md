# 语言能力范围与演进原则

Chaos 是图灵完备、语义自洽、可组合的智能体函数语言。用户用函数、数据、模块和受控效果构造复杂程序；Agent 是可调用组件，Bot 组织多轮目标，Process 保存长期状态。语言不以补齐 Python／Lisp 的全部功能、标准库或第三方生态为完成条件。

[语言核心合同](LANGUAGE_CONTRACT.md)规定可依赖的组合关系，[语言参考](LANGUAGE.md)规定语法，[计算语义](SEMANTICS.md)给出抽象计算能力的构造说明。软件包版本由 `chaos/version.py` 管理，方言版本、结构 IR 和各编译产物 ABI 分别管理，不能互相替代。

## 当前可依赖的能力

| 能力 | 入口与合同 |
| --- | --- |
| 计算与数据 | 一等函数、递归／互递归、闭包、let／var、尾调用、严格 Bool 条件、循环、模式、List／Tuple／Record／Set／HashMap、数值与契约，见[语言参考](LANGUAGE.md)及[组合契约](CONTRACTS.md) |
| 对象与高阶组合 | C3 类、方法、描述符、运算与迭代协议、泛型函数、apply／partial／compose；函数形成时保留名称快照，已有 Cell 共享，见[高级编程](ADVANCED.md) |
| 宏与模块 | 受限纯宏展开、卫生模板、封闭 import／export、Program 库组合；已编译对象可独立链接、携带显式宏接口和挂载到不同命名空间，见[宏](MACROS.md)及[编译设计](COMPILER_RUNTIME_DESIGN.md) |
| 挂起与并发 | 冷 Task／Generator／AsyncGenerator，显式等待或消费、TaskGroup、Channel、select、取消与资源排空，见[任务](TASKS.md)与[效果](EFFECTS.md) |
| 控制与错误 | 完整多返回值、异常／重抛、Condition／restart、动态上下文、prompt／escape／wind，以及同次执行内的同步可组合 continuation，见[多返回值](MULTIPLE_VALUES.md)与[Continuation](CONTINUATIONS.md) |
| Agent 与长期工作 | 普通 Agent、语言函数工具、Team、原生 Bot、Plan／Goal／Skill、Session 与 Process，共用原调度器、权限和事件，见[智能体语言](AGENT_LANGUAGE.md) |
| 外部环境 | 按名注册的 Computer／Device／Robot、观察和一次性命令；注册和查询不授予权限，传输回执不证明物理目标达成，见[Robot](NATIVE_ROBOTS.md) |
| 工具链与分发 | 源码／结构 IR、IDE／LSP、CLI／SDK／HTTP、调试、wheel／portable／项目目录；各入口沿用同一语义，不另建应用解释器 |

完整语法、参数范围和错误以对应专题为准。能力目录和测试数量不是语言完整性的百分比；提供方、平台、可选后端和硬件验收也不能相互代替。

## 编译与运行的边界

`compile_program`／`program_compile`／`Framework.compile` 产生静态分析报告，不是可执行程序。报告含保守潜在效果，不能仅凭函数名证明调用纯净。

`compile_function` 提供受守卫的标量／区域 JIT 和四种函数形式的 `auto-flow`／`native-flow`。Flow 后端选择真实控制后继，原帧继续执行动态调用、参数绑定、用户协议及效果。九类操作数和名称赋值／成员索引访问的子表达式也可递归降低；未被选中的子树保留宿主语义事件。当前不是所有语言操作都在机器码里执行，性能须单独测量。详见[原生控制流](NATIVE_FLOW.md)。

源码型 `artifact` 保存源码和可验证 Flow 模板，加载会重新运行前端校验；`executable` 保存已展开的封闭 AST，链接／加载不解析源码或展开宏，显式 JIT 才从加载后的 AST 生成计划。持久镜像运行复用原 Engine／RunHandle，记录实际镜像身份和授权项目身份，不能伪造源码来源。见[编译设计](COMPILER_RUNTIME_DESIGN.md)及[SDK](SDK.md)。

独立对象按内容绑定依赖。`libraries` 和 v3 纯宏接口允许只编译客户端源码；`mount_objects` 显式改变非标准模块身份，用于组合两个同名库。它们不提供依赖下载、自动版本求解或任意实现热替换。

可信宿主可显式使用私有认证的 native object 缓存，兼容进程重新链接已发射的标量／Flow 对象。默认不触盘，语言／HTTP 不获得任意机器对象加载权。这是本机对象复用，不是全语言 AOT 或跨目标 ABI。

## 生命周期与权限

构造冷句柄不执行其主体；等待、消费或原宿主事件才准入效果。共享或借用值不产生额外取消、关闭或复制权。结果、完整多返回值、错误与生命周期身份必须在解释、编译及公开入口之间一致。

已排空的终态 Task／流可退役已消费输入及执行根，原闭合空游标身份、结果和错误仍可观察；未知或未排空运输保守保留。异常 traceback、返回值和共享 Cell 仍可能持有对象。这不是独立 GC、全语言 retained-graph 配额或无限任务创建能力。

同步 continuation 保存支持的解释后缀；原前缀不重放，每次调用重新执行后缀，既有 Cell 共享。活动 Task／流、编译状态、资源、lease、录制和未知 native 是明确屏障。长期恢复由 Process／Goal 的数据、检查点与协调合同承担，不能把活栈复制当作持久化。

## 验收与演进

优先修复破坏现有组合合同的问题：求值顺序、错误身份、多结果、权限、取消、清理、版本事务或跨入口语义差异。新语法、后端或外语协议须有明确用户需要和独立验收，不从比较表自动生成工作清单。

一次发布验证明确的源码版本、支持范围和真实运行证据：计算核心、高阶 Agent／Bot 组合、效果与生命周期、指导与长期状态、公共入口、分发和数据保全。确定性模型／工具与软件 Robot 夹具证明的是协议路径；付费模型质量、物理设备达成和性能分别验证。

抽象图灵完备论证依赖无界时间／存储模型；实现始终有预算。两计数器机示例及可替换指令表提供编码和有限执行见证，不是对全部程序正确性或终止性的机械化证明。

通用机器码 AOT、跨目标运行时 ABI、独立 GC、通用异步 continuation、自举与更广生态兼容是可选的独立技术方向。它们当前未完成，也不因图灵完备、有限测试通过或本页列出而成为下一次发布的必做条件。差异查阅[Python／Lisp 对照](PYTHON_LISP_CAPABILITIES.md)，实现基础查阅[运行时设计](COMPILER_RUNTIME_DESIGN.md)。
