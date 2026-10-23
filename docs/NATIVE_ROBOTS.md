# 原生 Robot、观察与一次性命令

Robot、RobotObservation、RobotCommand、RobotReceipt 是原语言的四种不透明原生值。可信宿主按名字注册资源；普通函数读取观察数据、选择通道值，再显式构造和等待一次命令。源码、结构 IR、Code／Program、AgentTool／Team 与异步流共用现有解释器、Task 和权限管线，不建立另一个机器人应用执行器。

## 注册和冷任务

项目须显式授予保留语言权限 `robot.control`，函数和 Team 还可收窄它。此权限不在默认授予集合中，也不能作为模型工具直接调用。注册名、静态 actuator 描述和 profile 的发现不调用工厂；源码不能提供串口路径或导入驱动。

```chaos example=robot-cold-task
import "std/robot" as R;
let cold=R.robot_open("mobile");
[type_of(cold),task_info(cold).state,R.robot_open==robot_open]
```

`robot_open`、`robot_observe`、`robot_act`、`robot_stop` 和 `robot_close` 都产生冷 Task。构造不会连接、读取或发送；await 才进入原生效果。`async with await robot_open("mobile") as robot { ... }` 沿原资源退出协议关闭租约。普通 with 不能驱动这些异步效果。取消、错误和 TaskGroup 收束沿原生命周期处理；不会重放已经发送的运动。

| 原语 | 参数与结果 |
| --- | --- |
| `robot_open(name)` | Task，等待后为 Robot |
| `robot_info(robot)` | 纯复制 RobotInfo Record |
| `robot_observe(robot, timeout_ms=1000)` | Task，等待一个有效完整包后为 RobotObservation |
| `robot_observation_info(observation)` | 纯复制 RobotObservationInfo Record |
| `robot_command(observation, channels, ttl_ms=250)` | 纯构造 RobotCommand，不发送 |
| `robot_command_info(command)` | 纯复制 RobotCommandInfo Record |
| `robot_act(robot, command)` 或 `robot(command)` | Task，等待后为 RobotReceipt |
| `robot_stop(robot)` | Task，等待停止传输回执 |
| `robot_close(robot)` | Task，真实退役后为关闭回执 |
| `robot_receipt_info(receipt)` | 纯复制 RobotReceiptInfo Record |

`std/robot` 的十个导出都是原全局 Native 的精确别名。Robot 直接调用、apply／partial、普通 async 函数和生成代码使用相同调用元信息。下面只定义控制器，不调用硬件：

```chaos example=robot-typed-controller
async fn cycle(robot:Robot)->RobotReceipt {
 let observation=await robot_observe(robot);
 let command=robot_command(observation,{left:0,right:0});
 await robot(command)
};
type_of(cycle)
```

这里的 left／right 属于下述 OpenBot profile；其他可信 RobotSpec 可声明其他通道，语言不靠固定轮子字段、动作文本或提示词猜测控制。

## 包、单位与函数策略

每个观察只包含一个实际收到的有效完整包。`readings` 是带单位样本 Record，每项精确包含 `value`／`unit`；整个包共享原始 `receivedAtNs` 和递增 sequence。没有把不同包的旧样本合并后改写成新时间，也没有通过无关帧刷新缓存。

以下普通函数可以读取原观察 info 的 readings；它只计算 profile 通道数据，示例输入是普通 Record，不是伪造的 RobotObservation：

```chaos example=robot-local-policy
fn channels(readings:Record)->Record {
 let sonar=get(readings,"sonar",{value:null,unit:"cm"});
 if sonar.unit!="cm" || sonar.value==null || sonar.value<30 {
  {left:0,right:0}
 } else { {left:0.2,right:0.2} }
};
[channels({sonar:{value:80,unit:"cm"}}),channels({sonar:{value:null,unit:"cm"}})]
```

原生句柄受解释器、运行周期、当前租约和权限交集检查。JSON、Process state 或历史报告只保存诊断数据，不能恢复活观察、命令或资源。可持久保存注册名及普通策略状态；每轮重新打开资源并取得新包。

## 命令的新鲜度和消费

RobotSpec 的 actuators 是可信声明：通道名各有 `minimum`／`maximum`／`unit`。channels 必须精确包含当前 profile 的全部声明通道；值为有限 Int／Number，拒绝 Bool、未知字段和越界值。最多 16 个通道；观察 readings 最多 32 项。语言不会将任意 Record 当作可调用运动指令。

命令绑定精确观察、lease、generation 和 sequence；观察必须仍是最新有效包。`ttl_ms` 为 1–1000 的 Int，默认 250；`timeout_ms` 为 1–10000 的 Int，默认 1000。宿主的 `maxObservationAgeMs` 为 1–10000，默认 1000，源码不能提高它。

过期时刻取原 `receivedAtNs + ttlMs` 与运行期限的较早者。宿主准入与适配器真正写字节前都检查同一绝对时刻和观察 sequence。保存冷 Task、延迟 await 或收到其他包不会给旧命令重新开始 TTL。别名和权限投影共享一次性消费状态；已消费、外来、过期、录制或权限不足的值不能绕过检查。

纯宏不能执行这些运行时资源函数；quote／unquote 可生成普通原生调用 AST。下面的宏只在运行阶段构造冷 Task：

```chaos example=robot-runtime-ast
macro emit(run,name){quote{unquote(run)(unquote(name))}};
import "std/robot" as R;
let cold=emit!(R.robot_open,"mobile");
[task_info(cold).state,R.robot_act==robot_act]
```

## 公开元信息

四种原生值保持不透明；info 函数返回独立的有界 Record。字段合同由同一 NativeSpec 提供 IDE／LSP 类型与参数提示，分析、格式化、编译和结构编辑不连接设备或执行策略。

| Info | 精确字段 |
| --- | --- |
| RobotInfo | id、name、generation、profile、status、actuators、maxObservationAgeMs、latestObservationId |
| RobotObservationInfo | id、robotId、generation、sequence、receivedAtNs、readings |
| RobotCommandInfo | id、robotId、generation、observationId、channels、ttlMs、used |
| RobotReceiptInfo | schema、id、robotId、name、generation、operation、status、targetVerified、commandId、observationId |

latestObservationId 可为 null；无对应命令或观察的回执 ID 可为 null。端口、适配器、控制上下文、能力与内部 lease 不公开。静态注册清单不证明连接成功。

## OpenBot 串口 profile

用户确认的来源为 [isl-org/OpenBot（研究仓库 ob-f/OpenBot）](https://github.com/ob-f/OpenBot/tree/216ecb018ebe26cfae68f15ed42397493d5861c6)，固定提交 `216ecb018ebe26cfae68f15ed42397493d5861c6`。协议依据其 [openbot.ino](https://github.com/ob-f/OpenBot/blob/216ecb018ebe26cfae68f15ed42397493d5861c6/firmware/openbot/openbot.ino) 与 [Vehicle.java](https://github.com/ob-f/OpenBot/blob/216ecb018ebe26cfae68f15ed42397493d5861c6/android/robot/src/main/java/org/openbot/app/robot/vehicle/Vehicle.java)，没有宣称移植全部 Android 应用。

OpenBot profile 声明 left／right 范围 -1..1、单位 normalized，驱动转为 `c` 帧的有符号整数。有限固件心跳在任何运动前配置；独立 I/O worker 串行拥有写入和 TTL watchdog，不执行语言策略或保留已经完成的 open Task 上下文。遥测 voltage 使用 V，sonar 使用 cm，leftRpm／rightRpm 使用 rpm；sonar 65535 转为 null。调试、未知、vmin 或无效帧不产生新观察。

固件没有关联运动请求的 ACK。`sent` 只表示完整命令帧提交，`stopSent` 只表示完整停止帧提交，`closed` 只表示资源退役；targetVerified 始终 false，不证明车轮移动、实际停止或导航成功。未知部分写入不能追加 stop／重同步或重试；停止心跳并退役连接，保持明确错误。运行中重置撤销旧连接权威，不能自动复活 generation。

成功实际关闭后才释放 Engine 内共享物理租约；关闭失败保留隔离。串口资源按规范路径共享，Device／HID／Robot 别名不能竞争同一端点。这是进程内资源合同，不是跨主机分布式锁。假驱动、模拟串口或 PTY 验证软件协议，不是物理车辆验收。

可信 Python 宿主可用 `Registry.register_robot(RobotSpec(...))`，或 `openbot_robot(name, port, ...)` 注册该 profile。`load_robot_registry(path, registry=None)`、CLI `run --robots`／`serve --robots` 使用同一配置入口；可与旧 `--devices` 合并，注册不授予权限。公开 SDK 的源码、IR 和 capability 接口沿原 API v1。

配置使用严格的 robots 列表；每行只接受 transport（默认 openbot-serial）、name、port 及可选 baudrate／timeout／telemetry_ms／max_observation_age_ms。最多 32 行、64KB，重复名称与额外字段在注册前整体拒绝；加载不打开串口。公开工厂 openbot_robot 的默认值为 baudrate=115200、timeout=2.0、telemetry_ms=100、max_observation_age_ms=1000。

完整的原生策略／资源程序见[Robot 程序](../examples/native-robot.chaos)；配置样例见`examples/openbot/robots.example.json`。其硬件调用应只在已注册、已授权的宿主执行，默认文档检查只执行本页四个无硬件片段。Android 摄像头／视频桥、视觉模型和导航适配仍未实现；HID 与 Computer 的区别见[设备](ROBOT_DEVICES.md)。
