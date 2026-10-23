# 一等 Code 与运行时函数构造

0.76 将代码表示纳入语言值。Code 保存不可变的规范源码及 chaos.program/1 IR；Code 不等于正在运行的 Function。六个原生函数使用已有解析器、格式化器、宏展开器、IR 编辑器和函数调度器，避免通过关键词猜测程序。

```chaos example=code-basic
let c:Code=code_from("fn(x:Int)->Int{x*factor}");
let f=function_from(c,{factor:2});
[f(21),type_of(c),code_info(c).parameterCount]
```

| 函数 | 合同 |
| --- | --- |
| code_from(source) | String 或公开 Program IR；一个函数表达式或命名 fn 声明，可包含纯顶层宏 |
| code_source(code) | 返回规范源码 |
| code_ir(code) | 返回独立的可编辑 IR 数据副本 |
| code_edit(code, operations) | 使用 program_edit 的节点编辑操作，验证后返回新 Code；旧值不变 |
| code_info(code) | 七字段只读副本：schema、sourceHash、semanticHash、flavor、name、parameterCount、nodeCount |
| function_from(code, bindings={}) | 通过原 AST 构造当前执行上下文中的真实 Function；显式捕获 FunctionBuildFrame 保存完整环境、独立 AST 与待决求值；CallableSyntaxFrame 保存四类函数默认值和形成阶段。生成源码登记仍沿原次序；帧不重复默认值或藏入整个旧构造生成器。详见[执行状态](EXECUTION_FRAMES.md#函数形成与泛型方法组合的执行状态)。

bindings |

```chaos example=code-edit
let old=code_from("fn(x){x+1}");
let literal=code_ir(old).root.statements[0].body.statements[0].right;
let changed=code_edit(old,[{op:"set",id:literal.id,field:"value",value:2}]);
[function_from(old)(3),function_from(changed)(3)]
```

构造支持普通 fn、async fn、fn*、async fn*，以及参数合同、默认值、解构、位置专用、关键字专用、剩余参数和命名递归。Code 解析、查询和编辑均不运行默认值或函数体；每次 function_from 按原函数定义规则计算一次默认值，包括合法的 await。调用函数不会重算已确定的默认值。

bindings 浅复制名称关联，值保留原有身份；可变 Class 实例的字段写入仍通过原协议被观察。原生 List／Tuple／Record 是值式容器，不支持成员或条目赋值。替换调用者 bindings 的关联不会重新绑定已经构造的函数。没有隐式读取调用者局部变量，也不会捕获调用者的 var 单元。构造每次获得新的 AST，运行旧函数不会被 Code 编辑影响。生成函数可以直接作为 AgentTool、Team 角色和高阶组合回调，也可以传给 compile_function；不新建另一解释器或使用宿主 eval。

Code 属于创建它的 Evaluator，纯语法值可在同一 Evaluator 的后续轮次使用；Task、Generator、Context 等 bindings 仍通过既有活轮次／所有权检查。跨执行上下文请传递源码或 IR，JSON 的 $code 摘要不会恢复活代码句柄。代码构造不扩大工具权限，效果、预算、取消和资源清理由原运行时处理。源码上限 100 KB，公开数据上限 20000 节点，bindings 上限 256 项。

编辑器从静态 code_from 字符串推导生成函数参数，不执行默认值；动态输入与 code_edit 后的签名保守为未知。Program、CLI、SDK、HTTP 与 IDE 执行使用同一六个原生函数。0.77 在 function_from 构造前登记只读生成源码，可在已登记文件设置断点、单步并查看调用栈；它不成为项目模块或可编辑草稿。编程助手可从 AGENT_LANGUAGE 主题学习该入口，单独 code-values 主题尚未接入其固定知识名单。

当前不提供任意模块 eval、运行时有副作用的宏、reader 宏、通用 continuation、活栈迁移或解释器自举。源码 IR 可以持久保存，活 bindings 不能由 JSON 重建。它是语言内自编程的阶段桥梁，不能据此宣称完整 Python／Lisp 已完成。


## 0.77 生成函数的原生源码调试

function_from 在验证 Code、显式绑定和语法之后、执行默认值之前，将规范源码登记到原运行时。code_from／code_edit／查询不会登记。注册表每个 Evaluator 和 DebugSession 最多64个唯一源、1000000字符，不驱逐活动函数的源；同标识冲突和覆盖固定项目文件会在默认值前报错。注册表只保存不可执行文本／语言版本／哈希，在 Evaluator 回收时释放，合法旧函数及嵌套函数跨同一 Evaluator 轮次仍保持来源。

调试器快照新增 generatedSources，按 code_ir(c).file 给出 source、languageVersion、sourceHash、semanticHash 副本，沿用秘密脱敏。虚拟文件名是带语言版本的内部不透明标识，不要解析其命名规则。初始 start_debug 仍只接受固定项目文件；函数构造登记后，原 debug_command 的 breakpoints 命令可以设置虚拟源断点。普通同步、async、生成器和 native-flow 调试沿原 AST／当前逻辑调用栈运行，暂停时间、取消和权限规则不变，不重放默认值或工具。

界面显示只读生成源码，使用自身语言版本和独立分析入口；项目 files、已保存 run.snapshot、IR 和编辑草稿不会因此改变。脱敏文本的字宽可能变化，显示列偏移不应视为未经脱敏原文的精确字符位置。API v1 和 SDK 调试命令不变。生成源码调试不提供跨异步／资源活栈持久化、自举、通用 AOT 或独立 GC；同步可组合 continuation 的独立合同见[Continuation](CONTINUATIONS.md)。


## 0.78 原调度器中的模块构造

`module_from(program, bindings={})` 对原生 Program 的封闭包创建独立模块实例，在当前 Evaluator、逻辑任务和效果权限内执行原 AST，返回与 import 一致的导出 Record。显式 bindings 浅复制名称关联、保留活函数／类／智能体的身份；不隐式捕获调用者局部变量。每次构造有独立词法单元和依赖缓存，同实例的菱形导入仅初始化一次，标准模块也不借用外层缓存。宏在原 ProgramBundle 中纯展开，运行时不会导出宏。顶层 await、四类函数和原 AgentTool／Team／群体回调可组合。

代码不是宿主 eval，也不是 run_program 的 JSON 后台任务。构造失败不返回完整 namespace，不自动重试，更不回滚已完成的外部效果。模块返回值中的冷 Task／Generator 不被额外启动。模块及导出函数中的 current_program 返回该封闭定义且 origin 为 null；项目采用和持久演进仍需原 Program／Process 合同。相同定义的 data 标签按源码身份匹配，Class、词法状态和缓存按实例独立。

原生成源码表继续保留四字段。模块全图先通过原64源／1000000字符配额的批量预检，再执行初始化与默认值；不能覆盖固定项目文件。额外 generatedSourceContexts 是纯逻辑文件／虚拟文件映射和版本数据，IDE 在只读源视图中分析完整自有文件包，标准库由原解析器解析，不写项目、快照或编辑草稿。受信自定义 source_handler 的外部副作用不承诺回滚。

这不代表完整外语兼容、reader 宏、通用跨边界 continuation、活栈迁移、自举、AOT 或自研 GC 已完成。

模块构造、依赖加载、import／export 及实际导出调用现在使用[真实模块算法帧](EXECUTION_FRAMES.md#模块初始化导入与导出入口的执行状态)。这不改变每实例缓存、显式活值绑定、完整源图预检、失败清理或已发生效果不回滚的规则；纯源准备和原调度周期仍保留各自边界。
