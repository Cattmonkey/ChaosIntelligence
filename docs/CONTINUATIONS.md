# 可复用的定界 Continuation

本页规定 0.138 的有界组合式 continuation 语义与可依赖边界；各次实际测试、安装与分发以独立交付证据为准。它与 Mode／Goal／Skill 的生命周期是不同能力，不宣称完整 Python／Lisp、GC 或 AOT 已完成。核心能力组合见[语言核心合同](LANGUAGE_CONTRACT.md)。

## 捕获、组合与显式退出

`capture_continuation(tag, receiver)` 以最近的同身份 `call_prompt(tag, body)` 定界，保存原执行游标上从提示到结果孔的受支持控制段。它在原位置调用 `receiver(k)`；receiver 的完整返回值成为 capture 调用的返回值，初始后缀继续执行。它不会隐式中止原提示。

`k(*items)` 将一次新的保存段实例组合在当前调用者之上，把全部位置实参作为结果孔的完整完成，段结束后返回当前调用者。命名实参不接受。每次调用都有新的可变帧、参数草稿、待决值和激活锚点；活动 Env 命名空间外壳按次重建，既有 Cell 和普通语言对象的身份仍共享。保存段不是缓存函数结果，也不是重新运行源码前缀。

`continuation_info(k)` 只读返回 `id`、`state`、`available`、`frames` 和 `invocations`。查询不调用保存段；invocations 记录已成功安装的组合段次数，不计初始后缀；before 失败且未安装时不递增，失败尝试仍消耗原运行预算。Continuation 是真实可调用值，不公开任意帧修改或 invoke-by-id 能力。

`receiver` 可以显式执行 `abort_prompt(tag, value)`，形成 shift 风格的退出；保存段不依赖已关闭的旧 PromptScope，之后仍可在原合法 holder／epoch 内多次调用 k。旧 `call_escape` 仍是活动动态范围内的退出，范围关闭后拒绝调用，并未改成可复用 continuation。

## 前缀一次，后缀按显式调用次数运行

`examples/continuations226.chaos` 的候选程序：

```chaos
var saved=null;var prefix=0;var suffix=0;let tag=prompt_tag("saved");
let initial=call_prompt(tag,fn(){prefix+=1;let value=10+capture_continuation(tag,fn(k){saved=k;2});suffix+=1;value});
let first=saved(30);let second=saved(40);[initial,first,second,prefix,suffix,type_of(saved),continuation_info(saved).invocations]
```

按所选合同，结果应为 `[12,40,50,1,3,"Continuation",2]`。初始 receiver 返回2，原后缀执行一次；保存段分别注入30和40，再执行两次后缀。prefix为1，suffix为3。这个预期是行为契约；实际通过情况应以候选运行证据为准。

后缀中的工具、模型或软件 Robot 动作会在每次明确调用时重新请求原宿主准入。它们不是旧回执的缓存重放，也不意味着可以重复使用一次性命令。应在捕获前完成且退役前缀资源；活动 Robot／Device／Computer 租约不能被复制进保存段。

## 完整结果与共享状态

零结果、单个 null、一个 List 和多个结果保持不同。Continuation 的位置实参经原 Completion／所有权／能力校验后才注入结果孔；原普通首项投影、完整结果接收和函数返回契约仍在各自语义位置执行。嵌套普通调用中的已求值操作数不重新执行。

可复用不等于快照隔离。普通函数形成时对当时词法名称作快照：之后才声明的名称不会自动进入旧闭包。capture 后声明的新局部与返回闭包属于每次新后缀的命名空间；保存段看到的是既有共享 Cell 的当前值；调用者在两次调用之间修改 Cell，下一次后缀就读取新值。每次的控制位置和可变执行容器独立，不能为模拟隔离而复制 Cell，也不能为恢复控制而回放原函数前缀。

## Wind 的真实新激活

初始捕获不退出当前 wind。组合调用保留调用者原有活动范围，并为保存段中的 wind 建立新的激活：before 成功后才登记该激活，完成后只执行该激活的一次 after。即使 k 在 receiver 内调用、调用者仍处于同一段 wind 代码，新增的保存段也是另一份激活，不能按回调或描述相同把两者合并。

例如 receiver 内直接调用 k 的合同顺序为：原 before、新 before、保存段主体、新 after、原主体继续、原 after。原调用者没有被退出再进入；新增激活仍有自己的进入／退出责任。嵌套保存 wind 按外到内进入、内到外退出。

before 失败不能安装半段或运行该次 after；after 失败退役其真实激活后按原错误规则传播，后续合法组合仍须保持 LIFO、源位置和权限状态。ResourceLimit、硬取消和原生 GeneratorExit 仍遵循原硬控制策略，不为再进入启动额外用户清理。

## 可支持范围与屏障

正向范围是同 evaluator、同活动 epoch、同实际逻辑 holder 上的同步解释段：普通函数调用、操作数／块／分支、while、普通保护主体／处理器和 wind body。每种准确帧类型与阶段都有显式保存合同；原帧执行复制出的后缀，不引入另一个求值器或 scheduler。

当前以下情况是明确的 fail-closed 屏障：

- async／generator／async-generator 活动段、Task／stream holder 和跨 Task 调用；
- 活动编译 FlowState／未支持的原生或便携编译段；不以从头解释伪装恢复；
- for 迭代句柄、已获取资源、ExitStack、TaskGroup／成员、Mode／Goal lease／录制状态；
- 任意未知 Python native iterator／ProtocolBridge、未决外部效果或不能复制的宿主协议；
- wind 进入／退出回调、正在展开的错误／控制转移及未注册的帧或字段。

屏障检查应先于改变当前栈、调用 receiver、进入新 wind 或退出原资源。失败不取得借用 Task／stream 的额外取消或关闭义务。所有权和 epoch 失败使用 ownership_error；不支持捕获的边界使用 continuation_barrier_error。资源额度属于原 ResourceLimit 高优先级合同，而不是可吞掉的普通业务错误。

调用权限取保存时上限、当前调用者和投影视图的交集。保存的源码定义、帧结构和完整参数都要在安装前验证；不得用 continuation 扩大权限或绕过契约。当前实现不承诺跨 Task、跨 epoch、跨请求或跨进程迁移。

## 预算、工具链与分发

图像帧上限128、图像创建计数上限1000、每图像成功安装组合段次数上限1000，另受既有捕获图、参数、深度、步数、时间和取消限制。复制工作使用当前运行的预算，不新建 evaluator／epoch，也不重置 steps。边界位置及硬错误优先级需要实际测试验收；没有性能证据则不声称提速。

源码、结构 IR、Code、SDK 和 CLI 使用同一原生调用入口；IDE／analyze／format／编译报告不执行 receiver。公开目录新增两个全局原语和一个 Continuation callable 规范，当前为335个全局 Native／381项完整规范。335不等于全部公开成员或整个语言能力数量。

便携编译 artifact 继续保存和重验源码计划，不保存活 Continuation、Env、Cell、权限或 native 地址。加载含 continuation 程序的源码计划，仍在该次实际运行的 epoch 内创建新值；JSON 投影不授予跨请求调用权。没有新增可复用 continuation 的 HTTP 会话接口、跨重启活栈、AOT、独立 GC 或完整 Python／Lisp 生态承诺。

## 理论参考与明确差异

语义参考 [Racket 的 call-with-composable-continuation 文档](https://docs.racket-lang.org/reference/cont.html)。本合同采用组合而非隐式中止；显式 abort 可表达退出。Chaos 限制同 evaluator／epoch／holder，并为每次组合建立新 delimiter 和 wind 激活，屏障也更保守。Racket 可跨线程使用的规则不是本系统的能力；不宣称 Racket／Scheme 的完整兼容。

实际验收必须分别保留非平凡多次调用、完整结果、共享 Cell、wind 正／负例、线性屏障、owner／epoch／权限、额度、Agent／软件 Robot 效果和公共入口证据。一个成功字面量示例不构成整个 continuation 合同或完整语言完成声明。
