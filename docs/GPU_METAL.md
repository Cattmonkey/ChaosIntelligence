# 函数到融合 Metal 内核

0.62 在既有 GPU 数值 Task 上增加真正的纯算术函数编译：语言 AST 降为不可变 Float32 IR，生成一个 Metal 内核，在独立后台子进程中编译并由 Mac M 系列 GPU 执行。它使用普通函数、call 和 await，外层智能体函数、AgentTool 和 Team 继续使用原语言运行时。

默认 `compiler="tensor"` 保持 0.61 行为，`await` 的数值结果仍精确包含 values、device、backend、dtype、fallbackReason 五字段。编译证据通过独立 `gpu_kernel_info(task)` 查询，不改变历史结果或 task_info 的字段。

## 显式编译与查询

```chaos
fn score(x: Number) -> Number { x * x + 2 }
let job = gpu_map(score, [1, 2, 3], device="mps", compiler="metal");
let before = gpu_kernel_info(job);
let result = await job;
{values: result.values, actualDevice: result.device,
 kernel: gpu_kernel_info(job), cold: before.status}
```

可用设备上的结果包含 `[3.0,6.0,11.0]` 和实际 `mps:0`；编译证据中的 compiler 为 metal、isolated 为 true、非空输入 dispatches 为 1。它们是运行后的事实；创建冷 Task 不加载后端、不编译或派发内核。

```chaos example=metal-cold-info
let job = gpu_map(fn(x: Number) -> Number { x * x + 2 }, [1], device="cpu");
let info = gpu_kernel_info(job);
[info.status, info.compiler, info.dispatches]
```

结果为 `["created",null,null]`。查询不启动 Task，也不把它标记为已观察。

| compiler | 行为 |
|---|---|
| tensor | 默认原路径：MPS／CUDA 使用 Tensor 运算，显式 CPU 使用便携 Python Float32 |
| auto | 实际设备为 MPS 且支持 compile_shader 时尝试融合 Metal；尚未派发的可恢复编译不可用可回退原路径，记录原因 |
| metal | 要求可用 MPS 和真实 Metal 编译；CPU、CUDA、缺少编译接口或编译失败都报 gpu_error |

device 的 auto 仍优先 CUDA、其次 MPS、否则 CPU；compiler 不改变此顺序。例如同时存在 CUDA 与 MPS 时，要求 Metal 应明确 `device="mps"`。运行错误、已经派发或进程异常不能通过重放掩盖。

```chaos example=metal-auto-cpu
let job = gpu_map(fn(x: Number) -> Number { x * x + 2 }, [1, 2, 3],
                  device="cpu", compiler="auto");
let result = await job;
[result.values, result.device, gpu_kernel_info(job).compiler]
```

结果为 `[[3.0,6.0,11.0],"cpu","tensor"]`；查询的 fallbackReason 表示编译选择，数值结果的 fallbackReason 继续只表示设备选择。

## 编译证据与编辑一致性

`gpu_kernel_info` 只接受当前执行上下文、当前周期、正确能力与录制范围的原 gpu_map Task。普通异步包装 Task、gpu_matmul 和 gpu_devices 不能冒充映射编译证据。返回的是可复制记录，不含源码、ShaderLibrary、Tensor、进程或设备句柄。

| 字段 | 含义 |
|---|---|
| requestedCompiler | 创建时请求的 tensor／auto／metal |
| compiler | 已完成的实际编译路径；尚无证据时为 null |
| status | 原 Task 实际状态，包含 created、running、suspended、completed、failed、cancelled |
| irHash | 创建时不可变 Float32 IR 的身份 |
| sourceHash | 实际生成 Metal 源码的身份；没有源码证据时为 null |
| sourceCacheHit | 本版生成 Metal 源码后为 false，没有跨请求编译对象或源码缓存；没有源码证据时为 null |
| dispatches | 真正融合内核派发次数；空输入为 0，非空成功 Metal 为 1；尚未知为 null |
| isolated | Metal 编译执行使用独立子进程；原路径为 false，尚未知为 null |
| fallbackReason | 编译回退原因；成功无回退为 null |

失败或取消只有在原调度器收到可信证据后才提交相应字段；状态为 cancelled 不能被当作已编译或已执行的证明。

```chaos example=metal-ir-change
let old = gpu_map(fn(x: Number) -> Number { x * x + 2 }, [1], device="cpu");
let fresh = gpu_map(fn(x: Number) -> Number { x * x + 3 }, [1], device="cpu");
let different = gpu_kernel_info(old).irHash != gpu_kernel_info(fresh).irHash;
let a = await old;
let b = await fresh;
[different, a.values, b.values]
```

结果为 `[true,[3.0],[4.0]]`。函数名不是缓存身份；源码或图形结构修改改变 IR／生成源码身份，已创建 Task 保留创建时快照。IDE 参数与字段提示、SDK、HTTP、CLI、Program 候选使用相同原生定义。文档代码可高亮、显示提示、格式化；复制始终取得原源码。

## 编译域与精度

编译域沿用 gpu_map：一个简单 Number／Any 位置参数、同步普通 Function、纯表达式或受限局部控制流，允许输入、有限数值常量、一元正负与二元 `+ - *`。分析实际 AST 和卫生参数绑定，不能提交任意 Metal 文本、调用工具、读取闭包或触发用户协议。0.63 的 IR2 可将局部绑定／赋值、严格 Bool、短路分支、while、字面量 for、break／continue／提前 return 编译进 GPU；详细范围与4096／1024预算见[GPU 控制流](GPU_CONTROL_FLOW.md)。异步和智能体推理继续在外层普通语言函数中组合。

最多 128 个 AST 节点、深度 64、10000 个输入和输出，每周期最多 64 个计算 Task。代码生成使用固定标识符与 Float32 位常量，生成有界 SSA 运算并检查中间值是否有限；不执行模型生成的宿主脚本。内核禁止浮点乘加收缩和重关联，但 GPU 可将次正规数清零，CPU／MPS／CUDA 不保证逐位一致。Float32 也不同于普通语言 Int 的任意精度合同，详见[GPU 数值精度](GPU_COMPUTE.md)。

空输入可以编译但不提交计算内核，dispatches 必须为 0。非有限输入、中间结果及输出被拒绝，错误不是有效数值结果。本版无跨请求 Shader 缓存，每次 Metal 请求有进程启动、PyTorch 初始化及编译开销；融合次数减少不能单独证明性能提速。

## 后台进程与取消

父进程只向固定可信 worker 传递有界 JSON IR 和数值。子进程不继承模型提供方配置或 key，不接收 Function／Env／Task；这用于隔离运行时和编译占用，不是执行任意不可信代码的安全沙箱。编译、设备派发、同步及销毁在同一子进程内完成，原有最多四个效果 worker 和可取消的 MPS 锁限制并发。

原 Task 等待期间，解释器其他逻辑任务和 HTTP 操作可继续运行；同步 Metal 编译不持有主进程的 Python GIL。取消在启动、等待锁、编译后及交付边界处理，已经提交的 GPU 内核不能被即时抢占。普通取消等待同步、IPC 排空和子进程回收后丢弃结果。驱动挂起等硬超时作为故障处理并回收进程，不能宣称这类故障已成功同步设备，也不会重放不确定的执行。

## 安装与实现范围

安装可选 GPU 依赖后，融合 Metal 还要求提供 `torch.mps.compile_shader` 的 PyTorch；本版硬件验收使用 macOS arm64、Python 3.13、PyTorch 2.14 和实际 MPS GPU。[PyTorch 编译接口](https://docs.pytorch.org/docs/2.14/generated/torch.mps.compile_shader.html)

```sh
python -m pip install 'chaos-intelligence[gpu]'
python -m chaos run examples/gpu-metal.chaos
```

随包脚本使用明确 CPU 路径，可在基础安装验证；页面第一个示例要求真实 MPS。旧 PyTorch 仍可运行既有 tensor 路径，基础 Python 3.9 保留 CPU 计算。CUDA 本版沿用 Tensor 后端，尚无 CUDA 代码生成或本机硬件验收；通用 GPU JIT、自动微分、完整张量库、AOT 与自研 GC 仍未实现。
