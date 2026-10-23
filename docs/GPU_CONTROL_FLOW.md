# GPU 控制流编译

0.63 把普通函数中的局部计算控制流编译进 GPU。它仍是 `gpu_map` 的显式 Float32 数值域，外层 Agent、AgentTool、Team、Program 与 Task 使用原语言合同；没有第二套提示词编程语言。

## 分支与循环

```chaos example=gpu-control-while
fn accumulate(x: Number) -> Number {
    if x < 0 { return -x } else {}
    var count = 0;
    var total = x;
    while count < 3 {
        count = count + 1;
        if count == 2 { continue } else {}
        total = total + count;
    }
    total
}
let job = gpu_map(accumulate, [-2, 0, 3], device="cpu");
let result = await job;
[result.values, gpu_kernel_info(job).compiler]
```

结果为 `[[2.0,4.0,7.0],"tensor"]`。改为 `device="mps", compiler="metal"` 时，函数体实际生成 Metal 分支和循环；需要真实可用 MPS 和 `torch.mps.compile_shader`。默认 tensor 路径在设备张量上逐步执行相同 IR，CPU 使用同一 IR 的便携数值解释器。

```chaos example=gpu-control-for
fn weighted(x: Number) -> Number {
    var total = x;
    for amount in (1, 2, 3) {
        if amount == 2 { continue } else {}
        total = total + amount;
        if total > 6 { break } else {}
    }
    total
}
let result = await gpu_map(weighted, [0, 3], device="cpu");
result.values
```

结果为 `[4.0,7.0]`。迭代器在本编译域仅允许最多 256 项的数值字面量 List／Tuple；`range`、用户迭代器或闭包数据仍在外层普通语言执行。

## 类型、作用域与执行次序

支持局部 `let`／`var`、对可变局部槽的 `=`、嵌套块、`if/else`、`while`、字面量 `for`、循环正常完成 `else`、`break`、`continue`、提前 `return`，以及 `+ - *`、一元正负、数值比较、Bool 字面量、`!`、短路 `&&/||`。条件必须是 Bool，Bool 不参与数值运算；局部槽的 Float32／Bool 类型固定，不允许跨类型赋值。函数仍需单个简单位置参数，参数／返回契约为 Number 或 Any，最终返回有限数值。

解析器和宏先生成真实 AST；编译器按词法绑定与卫生标识分配内部槽。内层名称可以遮蔽外层名称，内层赋值可修改可见外层 var，let 与 for 绑定不可写。每次进入块和迭代都重新初始化当地绑定。while 条件不能向该 while 或外层循环发出 break／continue，避免条件中的控制信号与设备循环目标不一致；将这些信号放在相应循环体。条件内部嵌套循环自己的 break／continue 仍由该嵌套循环消费。代码生成只使用内部数字槽标识和有限 Float32 位常量，用户名称不成为 shader 文本。

每个 lane 严格按表达式次序执行。未选分支、短路右侧、break／continue／return 后的语句不会计算或产生数值错误。循环 else 仅在正常耗尽时执行；break 跳过 else，return 直接结束整个函数。Tensor 执行使用活动 lane 掩码，不能通过先计算两个分支再选择结果来模拟短路。

```chaos example=gpu-control-lazy
fn safe(x: Number) -> Number {
    if x < 0 || (false && x * x > 0) { return -x } else {}
    x
}
let result = await gpu_map(safe, [3e38, -2], device="cpu");
[result.values[0] > 2e38, result.values[1]]
```

结果为 `[true,2.0]`；未选的乘法不会产生溢出。模型、工具、自由闭包、任意调用、用户运算协议、async／生成器、递归、一般异常处理不进入内核；编译校验直接拒绝，不能隐式调用宿主执行它们。

## IR、预算和错误

原有纯表达式保留 IR1 的 canonical、哈希域及生成源码。含控制流的函数使用版本化结构 IR2；父进程和 worker 各自校验节点、可见槽、可变性与类型，只传有界 JSON 和复制数值，子进程不接收 Function、Env、模型 key 或设备句柄。

结构 IR 静态上限为 128 个节点、深度 64 和 128 个槽（包括输入槽 0）；每个 for 字面量表另有 256 个有限数上限，表项不计入 128 个 IR 节点，但前端仍逐项校验与 tick；每次输入／输出最多 10000 项，每周期计算 Task 最多 64 个。IR2 每个 lane 最多访问 4096 个 IR 节点，在进入节点前扣费；每个动态循环调用最多执行 1024 次循环体。第 1025 次条件为 true 时失败，为 false 时正常退出；嵌套循环共享该 lane 的总节点预算。它们是设备计算的明确预算，与普通解释器的 tick 计费独立。

输入、常量与每次算术都使用有限 Float32；中间溢出不能被后续计算掩盖。各 lane 保留第一个执行到的错误，批量报告输入顺序中首个失败 lane 及对应 IR 节点。Metal 使用独立错误缓冲回传证据。Float32 舍入和设备次正规数处理的差异仍见[GPU 数值合同](GPU_COMPUTE.md)，不承诺与普通 Number 或任意精度 Int 逐位一致。

本版不承诺性能加速。循环预算是避免无限设备循环的语言后端合同，不意味着已提交内核可被即时抢占；取消仍须同步、排空和回收 worker。失败与不确定派发不能自动重放，空输入派发次数为 0，详见[Metal 证据与生命周期](GPU_METAL.md)。CUDA 使用 Tensor 适配，尚未实现 CUDA 源码生成或本机 CUDA 硬件验收。

## 安装和智能体组合

```sh
python -m pip install 'chaos-intelligence[gpu]'
python -m chaos run examples/gpu-control-flow.chaos
```

随包示例使用 CPU，可在基础安装验证。要使用 Mac M 系列 GPU，将任务改为明确 MPS／Metal；真实编译接口见[PyTorch 官方文档](https://docs.pytorch.org/docs/stable/generated/torch.mps.compile_shader.html)。源码、Program IR、CLI、SDK、HTTP 和编辑器运行同一函数，数值结果五字段与 `gpu_kernel_info` 九字段继续兼容。

把设备计算封装在普通 `async fn` 后即可作为 AgentTool 或 Team callable；系统智能体可以修改候选函数、用 `program_test` 验证再采用新版本。已经创建的 GPU Task 保持原 IR 快照，修改程序不会改写活动任务。
