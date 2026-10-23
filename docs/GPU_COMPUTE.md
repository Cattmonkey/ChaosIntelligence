# GPU 与 Mac M 系列计算

0.61 将批量数值计算接入语言内置函数和原有 Task 调度器。可选 PyTorch 后端使用 Apple Silicon 的 MPS／Metal GPU，或可用的 CUDA 设备；结果报告真实设备。普通函数可作为批量计算输入，普通异步函数可进一步成为 AgentTool 或 Team 角色。源码、Program、SDK、HTTP、CLI 与 IDE 使用相同接口。

默认路径调用 PyTorch 的设备数值内核；0.62 增加显式纯算术函数的[融合 Metal 编译](GPU_METAL.md)，0.63 扩展[局部状态与控制流编译](GPU_CONTROL_FLOW.md)，尚未从任意语言程序生成 GPU 机器码，也没有完成通用 GPU JIT、自动微分或完整张量库。没有性能基准则不承诺提速，小批量计算可能受传输及调度开销影响。

## 批量函数与矩阵

```chaos example=gpu-map-cpu
fn score(x: Number) -> Number { x * x + 2 }
let task = gpu_map(score, [1, 2, 3], device="cpu");
let result = await task;
[result.values, result.device, result.dtype]
```

结果为 `[[3.0,6.0,11.0],"cpu","float32"]`。调用只创建冷 Task，`await` 或 `spawn` 后才进行后台数值计算。`values` 是普通 List，返回的记录不包含张量、设备指针或活闭包。

```chaos example=gpu-matmul-cpu
let result = await gpu_matmul(
    [[1, 2], [3, 4]], [[1, 2], [3, 4]], device="cpu"
);
result.values
```

结果为 `[[7.0,10.0],[15.0,22.0]]`。两个参数必须是矩形二维 List，左矩阵列数等于右矩阵行数；零维、非有限数、布尔值和不规则行会被拒绝。

| 内置函数 | 返回 |
|---|---|
| `gpu_map(fn, values, *, device="auto", compiler="tensor")` | 冷 Task，完成值为数值结果 Record |
| `gpu_matmul(left, right, *, device="auto")` | 冷 Task，完成值为矩阵结果 Record |
| `gpu_devices()` | 冷 Task，完成值为设备可用性 Record |

数值结果字段为 `values`、`device`、`backend`、`dtype`、`fallbackReason`。`device` 来自实际执行张量，例如 `mps:0`、`cuda:0` 或 `cpu`；`backend` 为 `torch` 或 `python`，`dtype` 固定为 `float32`。CPU 回退有明确原因，不能将请求目标当作执行证据。

设备记录包含 `backend`、`version`、`cpu`、`mps`、`cuda`、`devices`、`defaultDevice`。检测也在后台进行，编辑器补全与分析不加载 PyTorch、不探测硬件。

```chaos
let available = await gpu_devices();
let task = gpu_map(fn(x: Number) -> Number { x * 2 }, [1, 2, 3], device="mps");
let result = await task;
[available, result.device, result.values]
```

在可用 MPS 设备上，第二项应为 `"mps:0"`。设备不可用时显式 `mps` 请求报错。示例要求已安装 GPU 可选依赖。

## 设备选择与安装

`auto` 优先选择可用 CUDA，其次 MPS，否则显式报告 CPU 回退。`gpu` 要求存在 GPU，`mps` 和 `cuda` 要求指定后端可用；这些显式请求失败时抛语言错误，不静默改成 CPU。`cpu` 使用便携 Python Float32 路径，无需 PyTorch。当前 CUDA 目标固定为设备 0，不修改宿主的全局当前设备。运行中的 GPU 后端错误不会自动重试另一设备。

基础安装仍支持 Python 3.9；GPU 额外依赖要求 Python 3.10+：

```sh
python -m pip install 'chaos-intelligence[gpu]'
# 源码目录开发安装：
python -m pip install -e '.[gpu]'
python -m chaos run examples/gpu-compute.chaos
```

Mac 使用包含 MPS 支持的 PyTorch；CUDA 环境须安装与设备和驱动匹配的 PyTorch 发行包。[PyTorch 安装入口](https://pytorch.org/get-started/locally/)

MPS 通过 Metal 执行设备运算。[MPS 后端说明](https://docs.pytorch.org/docs/2.14/notes/mps.html) CUDA 的设备工作异步入队；返回及清理需要同步。[CUDA 执行说明](https://docs.pytorch.org/docs/2.14/notes/cuda.html#asynchronous-execution)

本版已在 macOS arm64、Python 3.13、PyTorch 2.14 上验收真实 MPS 映射和矩阵乘法，并确认返回 `mps:0`。CUDA 选择与清理适配路径经过测试；本机没有 CUDA 硬件，尚未完成 CUDA 硬件验收。

## 精度、纯度与规模

这是显式 Float32 批量数值合同。输入转换、中间运算及输出使用 Float32，不改变语言普通 Int 的任意精度能力，也不等价于逐项调用原函数的普通数值语义。CPU 每个算术步骤按 Float32 舍入；GPU 的矩阵累加、融合及次正规数处理可能不同。验收采用适合样本量级的数值容差；例如常规样本使用 `rtol=1e-5, atol=1e-6`，并非任意问题的误差保证。需要精确整数时使用普通函数或[整数 JIT](NATIVE_FUNCTIONS.md)。

映射函数必须由当前 Evaluator 拥有，是一个简单位置参数、参数与返回为 Number／Any 的同步普通 Function，函数体可为既有纯表达式 IR1，或局部状态和控制流 IR2。支持参数、有限数常量、一元正负及 `+`、`-`、`*`；0.63 增加严格 Bool 比较／短路、let／var／赋值、if、while、字面量 for、break／continue／提前 return。分析实际 AST 及卫生绑定，不识别函数名称或提示词。Int 契约、自由变量、用户协议、调用、工具、模型、生成器和异步函数作为映射内核都会被拒绝；它们可以在外层语言函数中组合。类型与设备预算见[控制流指南](GPU_CONTROL_FLOW.md)。

函数最多 128 个节点、深度 64；单次输入和输出各最多 10000 个有限数；矩阵各维最多 256 且结果最多 10000 个数。每个求值周期最多创建 64 个计算 Task。创建时校验和复制数据，并捕获不可变数值 IR；修改原 List 或函数 AST 不会改变已创建的任务。

## 智能体函数与取消

```chaos example=gpu-async-wrapper
async fn calculate(values: List) -> Record {
    await gpu_map(fn(x: Number) -> Number { x * 2 + 1 }, values, device="cpu")
}
let result = await calculate([1, 2, 3]);
result.values
```

结果为 `[3.0,5.0,7.0]`。`calculate` 是普通语言函数，可直接绑定为函数工具：

```chaos
let tool = agent_tool("calculate", calculate, input_type="List", output_type="Record");
let bound = agent_bind(worker, [tool]);
await bound([1, 2, 3])
```

这里 `worker` 必须是已定义且配置提供方的 Agent；模型连接继续由系统设置管理。数值后端不接收 Agent、Env、Function、能力句柄或模型凭据。

GPU Task 使用已有有界效果线程池，等待数值计算时其他逻辑任务继续调度。直接 await 新建冷计算 Task 时等待者拥有该操作，取消等待者会取消操作；已经 spawn 的借用任务保持原有所有权。TaskGroup、录制范围和系统取消共同沿原运行时处理。

取消在加载后端、取得设备锁、传输、数值步骤及交付结果边界检查。已经提交到设备的内核不能被强制抢占；运行时等待设备同步清理后丢弃取消结果，再终结该工作。PyTorch 导入和同步期间也不能保证即时中断。不会为每个任务清空全局设备缓存或更改全局 GC／Torch 设置。[MPS 同步说明](https://docs.pytorch.org/docs/2.14/generated/torch.mps.synchronize.html)

更广的编译与异构目标见[演进设计](COMPILER_RUNTIME_DESIGN.md)；接口和自编程边界见[智能体语言设计](AGENT_LANGUAGE.md)。
