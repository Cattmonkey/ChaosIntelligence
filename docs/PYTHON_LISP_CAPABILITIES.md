# Python／Lisp 能力对照

本页将 Chaos 与 Python／Lisp 的常见能力作对照，用于理解语义选择、当前支持范围和生态差异。Chaos 的目标是图灵完备、语义自洽、可组合的智能体函数语言；有限发布验收见[语言核心合同](LANGUAGE_CONTRACT.md#h-一次发布的有限验收)，不要求覆盖 Python 或 Lisp 的全部功能。源码由自己的解析器形成 AST，再进入同一作用域、调度器、Program 和工具链；下表不把所有差异都列为必做功能，也不声明全部语言能力已经完成。

Lisp 有多个方言：Common Lisp 的对象／包／条件系统、Scheme 的 continuation、Racket 的模块与控制模型并不相同。需要采用其中某项能力时，应分别定义语义、兼容边界和验收；抽象图灵完备不能替代所承诺功能的实际证据。[Common Lisp 标准目录](https://www.lispworks.com/documentation/HyperSpec/Front/Contents.htm)、[Python 控制语句](https://docs.python.org/3/reference/compound_stmts.html)、[Racket continuation](https://docs.racket-lang.org/reference/cont.html)。

| 能力族 | Chaos 当前实现 | 差异、支持边界与可选扩展 |
| --- | --- | --- |
| 函数与递归 | 一等函数、闭包、默认／剩余／具名／位置专用参数、递归／互递归、尾调用、高阶组合 | 自动柯里化、全部外语签名／调用规则 |
| 多返回值 | 原生零／单／多完成、首项投影、显式 producer 捕获／调用／绑定／prog1／nth、Task 及纯宏传播 | 函数回调接口不同于 Common Lisp special forms；完整外语生态与跨异步／资源 continuation 仍未完成 |
| 泛型函数 | 真实 Class／DataType／内建契约与原生 EqlSpecializer 的多参数分派、固定 C3／从左到右优先级、持久方法表、primary／before／after／around、显式 primary next 与可用性查询 | 不支持任意谓词特化、自定义方法组合或完整 CLOS MOP；String 按值、Complex 保留实际类型的语义明确不同于 Common Lisp；after 不等待冷任务完成 |
| 运行时契约 | 内建／Class／DataType 与原生 List／Tuple／Record／Union／可空／HashMap 组合契约、闭合单一／互递归契约，既有命名注解和调用检查 | 无用户谓词、自动转换、内联泛型语法或完整静态推导 |
| 状态与数据 | let／var、任意精度 Int（受预算）、Number、原生 Fraction／Complex／有限 Decimal 及逻辑精度上下文、Rational／Real／Numeric 契约、List／Tuple／Set／Record／HashMap、标签数据、同步用户哈希与相等协议；项目记忆逐键版本读／原子CAS | 完整 Decimal API／非有限数值、Python dict 的全部 API、可变集合及完整数值库 |
| 控制流 | 严格 Bool if、while／for／async for、break／continue／return、耗尽 else、match、异常与重抛 | 不采用 Python 隐式 truthiness；动态 ContextVar、Condition handler／restart 与身份提示／动态 escape／向外屏障／wind 已有；同 epoch／逻辑 holder 的同步解释段已支持可复用组合；通用异步／资源 continuation 尚无 |
| 组合式续延 | capture_continuation、可调用 Continuation、continuation_info；同步解释段、完整多结果、共享 Cell、每次独立后缀容器与 fresh wind | 同 evaluator／epoch／逻辑 holder；Task／stream、活动编译、资源与未知 native 明确拒绝；无跨请求序列化 |
| 绑定与模式 | 声明／参数／迭代／资源／推导中的结构解构、原子批量赋值、类字段模式 | 每个 Python／Lisp 模式细节不逐字兼容 |
| 对象 | C3 多继承、方法、super、装饰器、property、用户描述符、动态建类／成员元信息、__new__ 与协作 __init_subclass__、原生 Type／元类、prepare 命名空间和可调用创建工厂、元类类型判断、一次基类适配与类参数化工厂、实例／元类五属性钩子与显式默认委托 | 析构器与完整反射生态 |
| 运算与容器协议 | 正向／反向／原位运算、比较、长度、成员、显式 bool、实例／元类条目读写删、__class_getitem__、无损 index | 原生容器值式更新；没有全部 Python 可变容器与协议，也未实现 typing.Generic／GenericAlias |
| 迭代与生成器 | 同步／异步迭代、共享游标、双向 yield、yield from、send／throw／close、四类推导、std/iter 同步／异步惰性组合 | 不提供重放缓存或完整 itertools／Lisp 流；跨 yield 活动 TaskGroup 等已列边界仍待扩展 |
| 语言级并发 | 冷 async Task、await、TaskGroup、Channel、select、等待与取消屏蔽 | 合作调度不保证 CPU 并行；跨进程通道、活栈持久化未实现 |
| 资源与异常 | with／async with、生成器资源工厂、动态资源栈、结构 catch／守卫、finally、Error／嵌套错误组／catch*、只读 cause／context 因果链、Exception 名义类／纯字段捕获 | 0.71 原生 Condition、动态 handler／restart 与真实展开恢复；原生 invoke_debugger 与有界交互式恢复已接入同一动态出口；完整 Common Lisp 条件类层次仍待实现 |
| 宏与元编程 | quote／unquote、卫生 AST 宏、纯编译期函数、模块宏导出／导入；v3 对象可携带纯宏接口，客户端没有库源码也沿原 Expander 展开 | 不实现所有 reader 宏或任意运行时编译期效果；旧 v1／v2 对象不猜测缺失宏定义 |
| 模块 | 封闭包、import／export、相对路径、标准语言模块、[Program 库挂载链接](PROGRAM_LINKING.md)、无源码展开 AST 镜像、内容绑定的独立运行时／宏库与显式编译对象命名空间挂载 | Python pip 包／Common Lisp 包系统不能直接导入；没有自动依赖求解或下载；替换库实现需重新编译依赖客户端 |
| 自编程 | 一等 Program、自身 AST 反射、候选修改／测试／CAS 采用、Process 原子演进 | 解释器自举、活栈迁移和任意自优化正确性证明未实现 |
| 智能体与环境 | Agent／Session／AgentTool／Team／Plan／Goal／Skill／Computer／Device／Robot 原生值及效果、Code／Program 和持久 Process | 原生 OS 桌面视觉后端、自动技能选择、物理 HID 复合拖拽／按住仍待扩展；浏览器与串口／Linux HID 共用原效果、权限和资源合同 |
| 工具链 | IDE／LSP、高亮／提示／格式化、文件树、IR 编辑、CLI／SDK／HTTP、断点单步与栈 | 当前全部 336 个全局原语与原生资源调用已有共享描述；提示仍为保守分析，完整静态类型推导和跨解释器调试未实现 |
| 编译 | 模块增量解析、静态潜在效果、可选 LLVM 精确整数区域 JIT、普通、async、generator 与 async generator 函数的可恢复 PC／结构 CFG、九类操作数与名称赋值／成员索引读写／完整键子表达式的递归控制降低、原调用与完整参数绑定的宿主事件组合 | 结构分支已由 LLVM 执行，叶子仍交回宿主；通用函数机器码、AOT、整程序指令重排／链接器优化尚无；0.73 已实现区域整数 DAG 调度 |
| GPU | Float32 map／matmul、真实 Mac MPS；IR2 局部状态／分支／循环可直接生成 Metal | CUDA 仅 Tensor 适配；通用 GPU 语言、自动微分、完整张量库尚无 |
| 内存与平台 | 宿主跨平台解释器、所有权和显式资源范围、已排空 Task／流的消费根退役、Python 宿主堆管理 | 自研 GC、跨目标 ABI 与通用异构编译尚无 |
| 标准库与部署 | 语言标准模块、签名更新、安装包、在线／离线文档 | Python／Lisp 完整标准库与第三方生态尚未覆盖 |

[属性协议](ATTRIBUTE_PROTOCOL.md)统一普通访问、动态角色代理与元类；纯模式、IDE 和诊断不执行钩子，目录仅由显式 dir 调用。

[组合契约](CONTRACTS.md)在现有函数和智能体调用边界纯检查数据；开放模板与闭合数据合同区分，递归检查有界且纯粹；描述值与载荷区分，不自动生成供应商 JSON Schema。

[通用键映射](HASH_MAPS.md)保留旧版本与键顺序，普通相等可有语言效果，纯模式匹配使用 Map 身份；Record 保持 String 键；[Set](SETS.md)共享用户键协议，纯宿主／宏路径不执行回调，协议集合的纯模式按身份比较。

## 下一步验收原则

所有扩展先进入语言 AST／运行时，再由 GUI、无界面运行、AgentTool、Program 和调试器投影；界面不另写关键词规则。新协议必须保持词法身份、效果次序、预算、所有权和取消清理。编译后端要对值、错误及执行路径做差分，不能只比较最终数字。

上表的差异不是必须补齐的发布任务。图灵完备说明抽象可计算性，不能推出全部外语功能、无限资源或任意任务成功；当前发布按语言自身的有限合同验收。已实现核心与资源边界见[计算语义](SEMANTICS.md)，范围与演进原则见[路线图](LANGUAGE_ROADMAP.md)。

原生能力使用三类合同：可调用工具、语言专属权限、平台事务。候选 fixtures 与生产工具调用使用相同分类；项目、角色与句柄的权限上限按交集保留，图形授权不会自动扩展它们。当前设计见[智能体与语言运行时](AGENT_LANGUAGE.md)，当前范围与兼容原则集中于路线图。

## 统一执行与可依赖的组合

[默认执行帧](EXECUTION_FRAMES.md)保存操作数、函数调用、循环、模式、保护与资源范围、对象协议、模块初始化以及 Bot／Plan／Goal／Skill 的实际待决状态。源码、IR、Code／Program、AgentTool／Team／Bot 共用原求值器和调度器，保持求值顺序、共享 Cell、完整多结果、能力、预算、借用、取消与资源清理。执行帧本身不代表所有活动状态都可以复制或持久化。

[原生控制提示](CONTROL_PROMPTS.md)提供 prompt、abort、动态 escape、屏障与 wind；0.138 的[组合式续延](CONTINUATIONS.md)已经实现同 evaluator／epoch／逻辑 holder 的同步解释后缀保存、重复组合和新 wind 激活。前缀不重放，每次后缀容器独立，已有 Cell 共享。活动 Task／stream、编译 FlowState、资源／lease 与未知 native 仍是显式屏障，不能据此声称任意 call/cc 或跨请求活栈恢复。

Agent、Bot 和 Robot 沿同一函数／效果合同组合。长期目标与动态上下文使用 Goal、Process 和持久指导邮箱；观察、一次性命令、回执与停止通过原权限和租约执行。物理设备成功必须独立验证，`sent` 不等于目标已达成。当前机器人范围见[原生 Robot](NATIVE_ROBOTS.md)，完整组合规则见[语言核心合同](LANGUAGE_CONTRACT.md)。

网页、SDK 和终端应展示同一持久事件和指导收据。普通 `chaos run` 保留最终 JSON 输出；`--stream` 用 JSON Lines 观察实际事件，`--interactive` 在 POSIX 终端中接收运行中指导，见[SDK 与终端指南](SDK.md)。它们不新增 Bot 执行器，也不改变语言语法、效果权限或共享预算。

## 独立技术目标与支持边界

独立编译库可用 `mount_objects` 在无源码、无前端调用的条件下组合到不同逻辑目录；库内部相对导入、宏定义位置和内容依赖仍由原验证器保证，标准模块共享。挂载是显式模块身份变换，不是依赖下载／版本求解、任意热替换或跨语言包兼容。

现有标量／整数区域 JIT 和四种函数的可恢复结构 CFG 是真实编译能力，语义叶操作仍由原运行时处理。 绑定右侧、调用的被调对象与实参、容器值、二元／一元运算和 do 子表达式中的结构控制可由 CFG 选择，原 OperandFrame 继续拥有求值次序、短路、多结果与尾调用；名称赋值、成员／item 的读写和复合赋值及 slice／多索引键也由原访问帧推进真实 CFG 子树，保留对象／键／旧值／右值顺序与提交契约；这项覆盖不代表动态调用或用户协议全在机器码中执行。便携编译 artifact 保存和重验源码计划；独立 executable 镜像则保存已展开的封闭 AST，载入／运行不重新解析或展开，显式 JIT 才按原规则降低。可信宿主可使用私有认证的 native object 缓存，在兼容进程中直接重链接已有标量／flow 机器对象；它不是任意来源二进制加载或全语言 AOT。

Flow 的内部 rooted-host-events/2 ABI 用类型化事件包和调用域中的代际 token 传递真实语义操作。native 与 portable 均先解析受验证的实际槽，再进入原帧；函数、作用域、结果及待决引用保存在同一调用的有界根槽中，结束沿原清理关闭该域。它保持原公开 compile_info／compile_flow_info 字段和旧底层 resume 二元组。根槽不是整个语言堆的追踪器，不给 Task／资源或 continuation 增加复制权，也不提供跨调用或 HTTP root ID 信任。

通用 AOT、全语言对象布局、跨目标运行时 ABI 仍须单独实现和验收。当前原生对象缓存和 typed Flow 事件均不能证明这些目标已完成。

已排空终态 Task／流释放已消费调用输入和执行上下文，原终值、多结果、错误、身份与调度元数据保持可用；活动或未知运输仍保守保留，借用目标不因退役被关闭。结果、异常及其 traceback、共享 Cell 或组元数据仍可拥有对象，原捕获图和每 epoch 数量上限也不变。释放已消费输入不等于准确追踪全部存活对象、语言堆配额或无限任务创建能力。

内存由 Python 宿主管理；`std/memory` 是持久 KV／CAS，不是堆回收器。独立 GC 仍需要对象布局、准确根集、宿主句柄固定、写屏障和安全点，不能用宿主 `gc.collect()` 或有限任务预算替代。异构编译、完整标准库、自举与外语生态的剩余差异以本页能力表和[编译运行时设计](COMPILER_RUNTIME_DESIGN.md)为准。
