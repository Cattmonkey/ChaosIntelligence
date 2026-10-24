# 从影片到可运行的研究程序

这组例子把《曲线之外》里的两种动力学交给同一个高阶积分函数：**模型是函数，参数由闭包捕获，结果可以再交给智能体审查。** 数值示例无需模型 Key。可选 8 角色示例会访问你配置的模型，和纯计算分开运行。

以下命令在 **Chaos 源码仓库根目录**执行，使用 `./bin/chaos`。需要 Python 3.9+ 作为运行时；使用包的入口为 `./chaos`，安装后的命令为 `chaos`。先完成[安装与环境配置](../../docs/GETTING_STARTED.md)。

## 先用命令行算一步

```sh
./bin/chaos run examples/research-lab/first-step.chaos --input-json '{"initial":[1,1,1],"dt":0.005}'
```

`--input-json` 接受 JSON **文本**，将其绑定为语言中的 `input`，不是文件路径。预期下一状态约为：

```json
{"next":[1.0031933311430772,1.129839801362649,0.9920510551862709]}
```

实际输出还包含初值和步长。这里的步长是模型无量纲时间，不是物理秒。

[first-step.chaos](first-step.chaos)把一个普通函数传入 [lorenz.chaos](lorenz.chaos) 的 `rk4`：

```chaos
import "lorenz.chaos" as Numerical;
let dynamics = fn(point) {
  let x = point[0]; let y = point[1]; let z = point[2];
  [10*(y-x), x*(28-z)-y, x*y-(8/3)*z]
};
{initial: input.initial, dt: input.dt,
 next: Numerical.rk4(dynamics, input.initial, input.dt)}
```

换成简化神经兴奋模型，同一个 RK4 不变：

```sh
./bin/chaos run examples/research-lab/neural/first-step.chaos --root examples/research-lab --input-json '{"initial":[-1.19940803524,-0.624260044055,0],"dt":0.05,"amplitude":0.8}'
```

预期 `next` 约为 `[-1.1598136851, -0.6241806805, 0]`。这里仅计算给定电流下的一步，不代表影片的完整脉冲实验。第三个零分量用于复用这个三分量积分器；神经模型的状态仍是 `v` 和 `w`。`--root` 指定包根，使子目录里的 `../lorenz.chaos` 导入留在同一个源码包内。

## 复现完整实验与独立核验

完整复现还需要 NumPy／SciPy，因为参考解、平滑、回归和交叉检查由宿主执行；候选积分、轨迹误差及向量场误差由 `.chaos` 程序执行。这一层明确使用 Python SDK，不把宿主数值工作伪装成语言自身计算。

在仓库根目录建立独立环境：

```sh
python3 -m venv .venv-research
.venv-research/bin/python -m pip install -e . numpy scipy
.venv-research/bin/python examples/research-lab/experiment.py
.venv-research/bin/python examples/research-lab/neural/experiment.py
```

Windows 对应解释器为 `.\.venv-research\Scripts\python.exe`。包安装需要可访问的 Python 包来源。完整复算可能用数分钟；脚本为较长的纯计算明确设置 SDK 步数与时间预算，不启动 IDE，也不请求模型。

脚本会在本例目录生成／更新 JSON、参考数组和验证记录。已有 [experiment.json](experiment.json)、[verification.json](verification.json) 与 [neural/verification.json](neural/verification.json)记录本次影片的实际验收结果；重新运行后以本机新结果为准。浮点末位可能随环境变化。

| 文件 | 作用 |
| --- | --- |
| [lorenz.chaos](lorenz.chaos) | 高阶 `rk4`、积分循环、系数闭包与误差函数。 |
| [validate.chaos](validate.chaos) | 接收验证输入，执行候选轨迹与误差归约。 |
| [input.json](input.json) | 第一组二次候选的保存输入，包含独立参考网格；适合由 SDK 读取。 |
| [experiment.py](experiment.py) | 合成数据、回归、8 次真实 Chaos 验证及 NumPy／SciPy 交叉检查。 |
| [neural/excitation.chaos](neural/excitation.chaos) | 固定脉冲边界，复用同一个 `rk4` 计算兴奋和恢复。 |
| [neural/experiment.py](neural/experiment.py) | 分段求独立参考，分别验证弱／强脉冲。 |

不要将长数组直接堆进命令行参数来代替完整复现脚本：操作系统有参数长度限制，默认运行预算也不等同于该实验显式设置的 SDK 预算。

## 再把证据交给智能体

[research-team.chaos](research-team.chaos)包含 8 个真正的 `provider: "model"` Agent。`equip` 接收 Agent 值，用 `agent_bind` 绑定普通 Chaos 函数工具；每次工具调用实际复算一个 RK4 步，并返回已验证的 Lorenz／神经模型记录。角色之间传递前序结果，再返回回答与工具回执。

先仅检查语法和导入；这一步不请求模型：

```sh
./bin/chaos check examples/research-lab/research-team.chaos
```

需要调用模型时，先按[模型配置文档](../../docs/MODEL_CONFIGURATION.md)保存自己的连接。以下命令读取[示例输入](agent-input.json)，使用私有配置并输出公开事件；运行受你配置的模型与执行预算约束，可能产生供应商费用：

```sh
./bin/chaos run examples/research-lab/research-team.chaos \
  --input-json "$(cat examples/research-lab/agent-input.json)" \
  --config /path/to/private-settings.json \
  --stream
```

这里的 `/path/to/private-settings.json` 需要换成你自己的路径；示例没有内置服务器地址或密钥。想先体验更短的单 Agent 调用，可从[五个模型示例](../../docs/MODEL_EXAMPLES.md)开始。

本次单独的付费调用记录中，8 个角色均完成执行并实际调用语言工具；**文本验收未全部通过**：2 条未通过额外的 80 总字符展示限制（其汉字数仍在原提示要求以内），1 条指标措辞需审校。详见[验收摘要](evidence/agent-acceptance.json)。它说明模型回答仍需检查，不能凭调用成功就接受科学结论。影片对白是审阅后的创作脚本，不是这些回答的直播或逐字引用。

## 怎样继续自己的研究

修改动力学函数、输入和评价函数，先定义能否接受候选的可复算标准，再让智能体提出建议。保留模型假设、参数、初值、时间网格与结果；把待检验的下一步和已执行的证据分开。

本例没有使用 GPU。Lorenz 是已知合成基准，FitzHugh–Nagumo 是简化兴奋机制，不是完整脑模型；有限初值与时间区间不能证明普遍正确。影片数据来源、关键数值与原论文见[制作及科学说明](../../docs/assets/research-film/README.md)。
