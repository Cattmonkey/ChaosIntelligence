# 树莓派 4B／5：USB 键盘和鼠标

在树莓派运行 Chaos，通过它的 USB-C 数据接口向目标电脑呈现标准 USB HID 键盘和鼠标。语言里的 Device、Task、AgentTool、Team 和 Process 使用原有语义；Linux 适配器是可信宿主注册的设备实现，不是另一套机器人执行语言。

```text
管理电脑 ── SSH／现有 API ── 树莓派上的 Chaos 程序
                                  │ Device(action)
                            Linux /dev/hidg* 字符设备
                                  │ USB-C 数据连接
                         目标电脑的标准键盘／鼠标驱动
```

管理连接使用树莓派的以太网或 Wi-Fi；目标电脑不需要安装 Chaos。树莓派可以承担后台角色与设备执行，模型规划仍使用本系统配置的提供方。外接键鼠提供输入能力，不会自动读取目标电脑屏幕；视觉观察需另行提供合法的显示捕获来源，不能把本功能称为完整远程桌面。

## 硬件与系统条件

Raspberry Pi 官方明确列出 **4 Model B 和 5 的板载 USB-C 口支持 gadget 模式**，其 USB-A 口不用于本方案。使用带数据线芯的连接线，启用 peripheral 控制器，并确认板型、系统内核和供电条件。[官方硬件说明](https://www.raspberrypi.com/news/usb-gadget-mode-in-raspberry-pi-os-ssh-over-usb/)、[官方 OTG 应用说明](https://pip-assets.raspberrypi.com/categories/685-app-notes-guides-whitepapers/documents/RP-009276-WP/Using-OTG-mode-on-Raspberry-Pi-SBCs)。

4B／5 的 USB-C 同时承担供电。电脑端口供电不足会导致重启或链路掉线；按对应板型和官方建议选择经过验证的供电方案，不使用未经核验的双电源并接接法。`--self-powered` 和 `--max-power-ma` 只声明 USB 设备用电属性，不会配置或增加树莓派实际电源能力。

目标通常可使用 Windows、macOS、Linux 自带 HID 驱动。键盘布局、USB 外设策略、登录环境及固件会影响行为；不保证所有系统、BIOS 或锁定环境接受设备。当前鼠标采用报告协议，提供五个按钮和滚轮，不承诺 BIOS Boot Mouse。字符输入使用显式 HID usage 和修饰位，没有按文字猜测键盘布局的转换。

本轮验证覆盖软件适配器、报告描述符和部署生命周期，**没有连接真实树莓派或目标电脑进行 USB 枚举与输入验收**。

## 准备树莓派

在树莓派安装实际取得的完整 Chaos wheel 或[可复制运行包](INSTALLATION.md)。本机 Linux HID 后端仅用 Python 标准库，不要求串口 extra；原来的 Pico／UART 后端仍可独立使用。

管理员在当前系统有效的启动配置中启用 `dtoverlay=dwc2,dr_mode=peripheral`。近期 Raspberry Pi OS 常用 `/boot/firmware/config.txt`，旧系统可能使用 `/boot/config.txt`；修改前确认实际路径、配置节和已有 OTG 设置，然后按系统要求重启。本包不自动编辑启动文件、加载内核模块或重启树莓派。

系统需提供 `dwc2`、`libcomposite`、HID function 和已挂载的 configfs。管理员可按系统条件加载 `libcomposite` 并挂载 configfs；检查 `/sys/class/udc` 是否有可用控制器，以及 `/sys/kernel/config/usb_gadget` 是否存在。已有 `g_ether`、`g_hid` 或官方 USB 网络 gadget 可能占用控制器，需由管理员明确处理；部署工具会拒绝覆盖它们。官方 [rpi-usb-gadget](https://github.com/raspberrypi/rpi-usb-gadget) 是 USB 网络实现，不是本工具的 HID 启用步骤。

## 检查、启用和关闭

在已安装 Chaos 的 Python 环境中执行只读检查：

```sh
python -m chaos.hid_gadget status
```

`plan` 生成配置和检查结果，不创建 USB 设备。下面的环境变量应由管理员填入实际设备的 VID、PID、唯一序列号及 USB 总线电流声明；`--max-power-ma` 是 0–500 的偶数毫安。示例的 `--self-powered` 仅适用于已经有独立供电的设备，不能靠加这个参数解决供电问题。

```sh
python -m chaos.hid_gadget plan \
  --vid "$USB_VID" --pid "$USB_PID" --serial "$DEVICE_SERIAL" \
  --max-power-ma "$USB_MAX_POWER_MA" --self-powered
```

确认计划后，用有 configfs 管理权限的账户，在**同一 Python 环境**中将 `plan` 换为 `enable`。有多个 USB 控制器时需显式指定 `--udc`。不要借用其他厂商身份作为产品默认 VID／PID。

部署使用固定名称 `chaos_hid`，仅管理通过完整结构和描述符核验的本工具设备。发生冲突时不接管已有 gadget；创建失败只回收本次创建的对象。`status` 根据内核公布的设备号查找键盘和鼠标节点，不能假定永远是 `/dev/hidg0`、`/dev/hidg1`。

先停止使用设备的 Chaos 任务，再用相同序列号关闭本 gadget：

```sh
python -m chaos.hid_gadget disable --serial "$DEVICE_SERIAL"
```

模块不自动安装启动服务、修改用户组或授予设备权限。管理员应仅把实际返回的 HID 节点授权给运行 Chaos 的账户；运行语言服务不需要拥有修改 configfs 的权限。设备节点权限及开机启用策略由该树莓派的部署管理维护。

## 连接原语言运行时

按 `status` 返回的实际节点填写 `devices.json`；以下路径只是形状示例，不能直接当作设备发现结果：

```json
{"devices":[{"name":"target","transport":"linux-hid","keyboard":"/dev/hidg0","mouse":"/dev/hidg1","timeout":2}]}
```

`transport` 省略时保留原来的串口配置语义，也可以明确写 `serial`。两种设备可以在一个清单中注册；非法条目或重名会在注册前拒绝。配置中的名字不授予权限，不允许传入模块名加载任意驱动。

在树莓派直接无界面执行：

```sh
chaos run robot.chaos --devices devices.json --capability device.control
```

`robot.chaos` 例如下面的设备程序。它会发送真实输入，只在接好目标设备且确认目标焦点后运行：

```text
async with await device_open("target") as device {
  let move = device_action(device, {action: "mouse_move", x: 10, y: 0});
  device_receipt_info(await device(move))
}
```

语言程序可以把纯策略函数登记为 AgentTool，供模型提出结构化动作，再由拥有 `device.control` 的 Team 普通函数执行。下面只生成数据，不接触设备：

```chaos example=raspberry-hid-spec
fn movement(x: Int, y: Int) -> Record {
  {action: "mouse_move", x: x, y: y}
};
movement(10, -2)
```

宿主 Python SDK 同样使用原注册表：

```python
from chaos import Registry, linux_hid_device
registry = Registry()
registry.register_device(linux_hid_device(
    "target", keyboard="/dev/hidg0", mouse="/dev/hidg1", timeout=2))
# 将 registry 传给原 Framework(registry=registry) 或 make_server(registry=registry)。
```

也可在树莓派运行 `chaos serve --host 127.0.0.1 --port 8892 --devices devices.json`，经已有 SSH 通道访问原网页和 v1 API。管理电脑例如建立 `ssh -L 8892:127.0.0.1:8892 user@raspberrypi`，随后使用 `http://127.0.0.1:8892`，本地转发和服务保持同一个端口以满足现有 Host 检查。项目仍须通过“文件 → 项目权限”把 `device.control` 加入草稿并保存，或用 SDK 明确授予。本功能不额外开放未认证的键鼠网络端口。

## Bot、函数与设备是一条调用链

同一个语言程序可以组合以下步骤：模型通过 AgentTool 调用由 `function_from(code_from(...))` 生成的纯策略函数；策略返回显式动作数据；Team 的执行角色接收该值；普通高阶函数 `fn(callable, value) { callable(value) }` 调用 Device，得到冷 Task；`await` 才发送报告。高阶函数还可以经过 `compile_function`，设备效果继续由原调度器执行。

这里的模型负责提案，语言负责按权限执行。保留能力 `device.control` 不能当成模型直接调用的工具；AgentTool 也不能借提示词取得它。承接执行的普通函数必须处于项目和 Team 共同授予该能力的作用域，名字 `main` 不增加权限。闭包可组合策略，源码与结构 IR 共用同一调用语义，不依靠回复关键词路由。

长期 Bot 用 Session 保持身份，用 Process 邮箱和状态跨轮接收任务；每轮重新获取 Device。改进时生成和测试纯策略或候选 Program，再经原版本采用机制更新。硬件效果不会用于候选探针来证明程序正确，也不把活设备序列化进下一版程序。相关可执行测试同时验证模型工具、生成函数、角色权限、编译调用、冷任务和实际适配器的软件报告路径。

## 输入、取消和回执边界

Linux 后端校验真实 HID gadget 字符设备，按端点取得独占使用权。语言里的 Device 仍是运行周期内的资源；设备、动作和回执不能通过 JSON 恢复为活句柄。设备工厂延迟到 `await device_open` 执行，加载配置、代码提示、格式化或构造冷 Task 都不会注入输入。

键盘报告为 8 字节，鼠标报告为 4 字节，与本工具发布的描述符一致。写入采用有界非阻塞等待；短写、超时、取消或连接错误均按不确定效果处理，不重新发送整个动作。每个动作和退出路径尽力释放按键与按钮，但无法撤销已经提交的输入，也无法保证断电后的释放。

`acknowledged` 在此后端表示适配器已经把报告提交到内核 gadget 队列，不表示目标电脑已经接收或完成应用操作；`targetVerified=false` 保持明确。需要闭环验证的程序应使用独立观察结果决定下一步，而不是只凭 ACK 判断任务完成。

物理 HID 作用于目标系统的当前焦点或全局输入，可能影响目标电脑上其他程序。后台隔离浏览器的虚拟光标属于 [Computer 后端](COMPUTERS.md)，两种能力各自使用原语言资源接口，但效果不同。角色、会话和自我修改流程见[机器人组合](ROBOT_DEVICES.md)。
