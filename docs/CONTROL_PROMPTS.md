# 身份提示、动态出口与控制范围

本页的“提示”指语言控制范围中的 PromptTag，不是发给模型的系统提示词；这些控制原语不配置模型地址、密钥或路由。模型连接见[模型连接与配置](MODEL_CONFIGURATION.md)，智能体请求循环见[控制平面与 Harness](HARNESS.md)。

PromptTag 是原生身份标签，name 只用于显示。`call_prompt`、`prompt_scope`、`abort_prompt`、`call_escape`、`control_barrier` 和 `dynamic_wind` 在原解释器的活动栈上执行控制转移。函数、源码、IR、宏生成代码、Code／Program、AgentTool／Team 和无界面接口共用此合同。

当前提供提示中止、动态范围 escape，以及同 evaluator／活动 epoch／逻辑 holder 内同步解释段的[可复用组合式 Continuation](CONTINUATIONS.md)。capture 在原位置调用 receiver，初始后缀继续；保存值的每次显式调用建立新的后缀激活。它不是通用 call/cc、异步活栈迁移或资源复制。完整组合原则见[语言核心合同](LANGUAGE_CONTRACT.md)。

## 身份与最近活动提示

`prompt_tag(name="")` 每次产生独立 PromptTag；同名对象不匹配。`call_prompt(tag, body, handler=null)` 安装标签身份，调用零参 body 并保留全部正常完成结果。`prompt_available(tag)` 只查询当前逻辑控制状态中可访问的活动目标；跨越 Chaos 向外屏障的目标返回 false。

```chaos example=control-prompt-identity
let tag=prompt_tag("answer");let other=prompt_tag("answer");
[tag==other,prompt_available(tag),call_prompt(tag,fn(){[prompt_available(tag),prompt_available(other)]})]
```

原生 PromptTag 只读 id／name。PromptScope 只读 id／tag／state／active；EscapeContinuation 只读 id／scopeId／state／active／available。IDE／LSP 的字段与调用提示来自共享 NativeSpec，不执行 body、处理器或出口。

## 中止与处理器

`abort_prompt(tag, *items)` 选择当前逻辑控制状态中身份相同的最近活动提示，将零／单／多个结果传到该边界。没有目标或目标被屏障阻挡时明确报错。普通语言 catch 不拦截内部控制转移。

目标移除并完成内层清理后，handler 才接收这些位置参数。省略 handler 时直接保留原结果；处理器本身也可返回多个结果或转移到更外层目标。若同一标签还存在外层范围，处理器可访问该外层目标。

```chaos example=control-prompt-abort
let tag=prompt_tag("result");
let normal=multiple_value_list(fn(){call_prompt(tag,fn(){return_values(42,"normal")})});
let escaped=multiple_value_list(fn(){call_prompt(tag,fn(){abort_prompt(tag,42,"abort");0})});
let zero=multiple_value_list(fn(){call_prompt(tag,fn(){abort_prompt(tag)})});
let handled=call_prompt(tag,fn(){abort_prompt(tag,20,22)},fn(a:Int,b:Int)->Int{if prompt_available(tag){0}else{a+b}});
[normal,escaped,zero,handled]
```

零结果仍区别于单个 null 或 List；普通绑定及外部输出采用[多返回值](MULTIPLE_VALUES.md)的首项规则。全部载荷组件在转移前验证、捕获与收窄权限，次项不能携带未经检查的活值。

## 动态出口与 wind

`call_escape(body)` 给 body 一个真实可调用的 EscapeContinuation。`exit(*items)` 直接退出其原动态范围，传递全部结果。出口只在创建它的活动范围内、当前相同逻辑控制状态且没有向外屏障时可用；范围关闭后拒绝调用。`escape_info(continuation)` 只复制五个公开字段，不公开 Env、处理器、权限或控制载荷。

`dynamic_wind(before, body, after)` 先验证三个回调，再依次执行。before 成功后才进入 body 并登记 after；before 失败不执行 body 或 after。正常完成、普通错误或有效非局部转移沿原展开路径执行 after，保留 body 全部结果。before／after 的全部结果经过验证再舍弃。ResourceLimit／硬取消等继续采用原有跳过用户清理钩子的规则。

```chaos example=control-escape-wind
var events=[];
let output=multiple_value_list(fn(){call_escape(fn(exit:EscapeContinuation){
 dynamic_wind(fn(){events=append(events,"before")},fn(){events=append(events,"body");exit(42,"done");0},fn(){events=append(events,"after")})
})});
[output,events]
```

这些回调的显式 async 函数体可在当前逻辑调用中合作等待；生成器函数与普通回调返回的冷 Task／Generator 保持冷值。不会为控制操作自动启动这些返回句柄。

## 流中的提示范围

`prompt_scope(tag, handler=null)` 创建一次性原生 with 管理器；as 绑定同一个 PromptScope。范围状态为 created／active／closed。流内已经进入的范围可跨 yield 保持 active，但消费者不因此获得该流的控制状态或中止权限。

```chaos example=control-stream-scope
let tag=prompt_tag("stream");var scope=null;
fn* stream(){let own=prompt_scope(tag);scope=own;with own as entered{yield prompt_available(tag);abort_prompt(tag,42,"stream")}}
let g=stream();let first=next(g);let outside=prompt_available(tag);let active=scope.active;
let done=next(g);
[first.value,outside,active,done.value,multiple_value_list(g),scope.active]
```

真实子 Task、Generator 和 AsyncGenerator 使用独立控制状态；透明协议执行与词法 TaskGroup 主体共享当前逻辑状态，组成员 Task 保持独立。外来、过期、跨逻辑范围的出口调用与记录／权限越界被拒绝。关闭、失败和硬控制继续收束原调度器拥有的资源，外部效果不会回滚或重放。

## 同一游标上的待决控制状态

函数式 prompt、escape、barrier 和 wind 与普通函数、资源范围及智能体回调使用同一执行游标。进入／主体／退出、活动目标、完整结果、待决转移和权限恢复保存在真实控制帧；处理器与词法 prompt_scope 共享同一处理器帧。显式 async 回调可挂起，暂停期间这些字段仍决定后续执行，不能用监控快照或效果重放代替。

下面的处理器在目标退出后运行，因此最后一个事件为 false；wind 的 after 先于目标处理器执行。结果为 `[[42,"handled"],["before","body","after",false]]`。

```chaos
var events=[];
let tag=prompt_tag("frames");
let result=multiple_value_list(fn(){
  call_prompt(tag,fn(){
    dynamic_wind(
      fn(){events=append(events,"before")},
      fn(){
        events=append(events,"body");
        call_escape(fn(exit){exit(41,2)});
        abort_prompt(tag,40,2)
      },
      fn(){events=append(events,"after")}
    )
  },fn(a,b){
    events=append(events,prompt_available(tag));
    return_values(a+b,"handled")
  })
});
[result,events]
```

回调正常完成、抛出和强制关闭都恢复进入前同一个逻辑执行者的权限。wind 在普通语言错误的退出回调中保留原动态错误上下文，并在回调退出或被关闭时恢复；硬预算、硬取消与强制关闭不新增用户退出调用。原所有权、epoch、记录范围与权限检查保持不变。普通 prompt／escape 的调用方式保持不变；新增 capture_continuation 与 continuation_info 通过同一调用入口实现受支持同步控制段的保存和 fresh wind 再进入。

## 向外屏障与工具链

`control_barrier(body)` 是 Chaos 的向外控制转移屏障。body 内新建的目标可以正常使用；跨屏障到外层 prompt 或 escape 被拒绝。活动状态可仍为 true，但 available 为 false。这个更强的向外合同与 Racket 的 continuation barrier 不同。

| 原语 | 参数与结果 |
| --- | --- |
| `prompt_tag(name="")` | 创建 PromptTag |
| `call_prompt(tag, body, handler=null)` | 身份提示的正常／中止结果 |
| `prompt_scope(tag, handler=null)` | 创建 PromptScope |
| `abort_prompt(tag, *items)` | 转移全部结果到最近可访问目标 |
| `prompt_available(tag)` | 当前目标可访问时为 Bool true |
| `call_escape(body)` | 传入 EscapeContinuation，返回正常／出口结果 |
| `escape_info(continuation)` | 五字段只读 Record |
| `capture_continuation(tag, receiver)` | 保存受支持同步解释后缀，在原位置调用 receiver(k) |
| `continuation_info(continuation)` | 复制身份、可用性、帧数与显式调用计数；不暴露执行根 |
| `control_barrier(body)` | 在向外屏障内执行 body |
| `dynamic_wind(before, body, after)` | 同一逻辑范围的进入／主体／退出回调 |

`std/control` 的十一个导出都是原全局 Native 的精确别名。宏可以用 quote／unquote 和显式依赖生成这些既有调用 AST；纯编译期不能创建或执行运行时控制范围。局部纯函数可以使用相同名称，名称拼写不会改变其行为。原始 IR、格式化、结构编辑和语言版本保持既有合同。

此设计参考 [Racket 提示与中止](https://docs.racket-lang.org/reference/cont.html#%28def._%28%28quote._~23~25kernel%29._call-with-continuation-prompt%29%29)、[动态范围出口](https://docs.racket-lang.org/reference/cont.html#%28def._%28%28quote._~23~25kernel%29._call-with-escape-continuation%29%29) 和 [dynamic-wind](https://docs.racket-lang.org/reference/cont.html#%28def._%28%28quote._~23~25kernel%29._dynamic-wind%29%29)。Chaos 的调用、屏障与硬控制规则以上述合同为准；没有宣称完整 Scheme／Racket 或 Python／Lisp 生态已完成。

完整语言控制示例见[控制提示程序](../examples/control-prompts.chaos)。

## Continuation design

本节现在对应[已实现的有界组合合同](CONTINUATIONS.md)，不再把全部可复用 continuation 列作未来功能。`capture_continuation(tag,receiver)` 保存最近同身份提示内的受支持同步解释段，在原位置执行 receiver；`k(*items)` 将一份新后缀组合到当前调用者，完整零／单／多结果经过原契约与权限检查。显式 receiver abort 可退出初始提示；保存段不借用已关闭的旧 PromptScope。

活动命名空间外壳每次重建，既有 Cell 与普通值身份共享；后续声明不会追溯加入形成时的闭包快照。wind 每次建立真实新激活：before 成功才承担一次 after，内层错误被 after 替换后，更外层清理看到当前替换错误及原因果链。调用者原 wind 不被移除或合并。

权限是保存上限、当前调用者与投影视图的交集。所有权、epoch、逻辑 holder、录制范围、定义、图像结构、参数和预算在安装前检查。跨 Task／stream、活动编译段、for 句柄、资源、TaskGroup、Mode／Goal lease、未知宿主桥及正在进入／退出或展开的状态保持明确屏障；不能把已完成 Agent／Robot 前缀重放来模拟恢复。后缀中的效果会按每次显式调用重新准入。

跨请求／进程持久化、异步或资源 continuation、通用控制栈复制、GC 和 AOT 仍不在此合同内。理论参考及与 Racket 的同 holder／fresh delimiter 差异见[Continuation](CONTINUATIONS.md#理论参考与明确差异)。
