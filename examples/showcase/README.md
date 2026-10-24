# 跟着首页动图操作

这两个示例与[项目首页](../../README.md)动图使用同一份语言代码，可在 Chaos 0.154.3 运行。无需模型密钥，不调用外部工具；`local` Agent 进行确定性的结构检查，不是大模型推理。

[▶ 播放／下载 40 秒演示](https://raw.githubusercontent.com/Cattmonkey/ChaosIntelligence/main/docs/assets/showcase/overview.mp4)：00:00 编写与排版 → 00:08 运行与事件 → 00:15 交互报告 → 00:26 网络与节点详情。视频为无配音的中文步骤字幕演示；下文提供完整操作步骤。

## 准备一个练习目录

从[最新发行版](https://github.com/Cattmonkey/ChaosIntelligence/releases/latest)取得 `Chaos-Project.zip`，解压到新目录。需要 Python 3.9+。在该目录运行：

```sh
./chaos setup --yes
./chaos
```

macOS／Linux 使用 `./chaos`；Windows PowerShell 使用 `.\chaos.cmd`。按终端打印的地址打开 IDE。将下列示例内容复制到练习项目的 `main.chaos`，再保存、运行；只选择另一个文件不会切换运行入口。自己的重要项目不需要为演示改写入口。

## 1. 格式化、运行与查看结果

打开 [format-run.chaos](format-run.chaos)，复制源码到练习项目的 `main.chaos`。想体验自动排版，可先把以下未排版版本放进编辑器：

```chaos
fn twice(value:Int)->Int{value*2};agent checker{provider:"local",mode:"review",tools:[]};let amount=if type_of(input)=="Int"{input}else{21};let answer=twice(amount);let checked=checker(answer);{answer:answer,review:checked.review.nonEmpty,provider:checked.provider}
```

1. 点击编辑器上方“格式化”，观察缩进、换行与代码配色。
2. 点击顶部“保存”，再点击“运行”。
3. 在底部“运行结果”中查看 `{"answer":42,"review":true,"provider":"local"}`，切换“执行事件”查看这次运行的公开过程。

`twice` 是普通函数；`checker` 是可调用的 Agent。这里的 `review: true` 只表示本地检查认为结果非空，不代表模型认可、测试全部通过或业务结论正确。

也可以把源码保存为 `format-run.chaos`，不打开 IDE，直接运行：

```sh
./chaos run format-run.chaos --input-json 21
```

输入 `10` 时 `answer` 为 `20`。未提供整数输入时，示例默认使用 `21`。

[查看动图](../../docs/assets/showcase/ide-workflow.gif) · [查看静态截图](../../docs/assets/showcase/ide-workflow.png)

## 2. 从函数计算生成报告与运行网络

打开 [live-report.chaos](live-report.chaos)，复制完整源码到练习项目的 `main.chaos`，保存后运行：

1. 点击底部“页面 / 报告”，用输出栏的“↕”按钮扩大查看区域。
2. 在“交付概览”看到指标、条形图和表格；向下滚动，点击“已完成”表头，观察排序。
3. 切换“真实运行网络”，点击“全屏网络视图”。可以缩放，并从“选择网络节点”中选择 `run · completed`，查看该节点的状态与来源。
4. 再点全屏按钮回到工作台。修改源码末尾的 `teams` 数据，保存并重新运行，报告会重新计算。

四组业务数据是演示输入，函数实际计算得到已完成 `102`、进行中 `15`、小组 `4`。它们不代表 Chaos 开发进度或真实组织业绩。

运行网络读取在线宿主提供的项目结构与运行记录。录制时显示 65 个节点、64 条关系，以及一个已完成的运行；节点数不是同时工作的智能体数量。`not-observed` 表示没有该节点的执行观测，不能据此推断它已执行或失败。网络是有界快照，事件数量与游标随运行方式、版本和会话变化。

报告本身是 `UIArtifact` 值；指标来自函数计算，`RuntimeGraph` 由在线宿主绑定运行。用 CLI 运行可取得 JSON 产物；在 IDE 中运行才能查看绑定当前运行的交互网络。离线 HTML 不会自动连接工作区。

[查看动图](../../docs/assets/showcase/live-dashboard.gif) · [查看静态截图](../../docs/assets/showcase/live-dashboard.png) · [生成式 UI 指南](../../docs/GENERATIVE_UI.md)

## 录制说明

视频与动图于 2026-10-24 使用 Chaos 0.154.3 的独立本地工作区录制。画面来自真实浏览器操作，按步骤选帧、添加标题字幕并调整停留时间；不是性能测试，也未使用生成图片替代界面。没有模型请求或私人项目数据。IDE／网络的 PNG 保留原始静态截图；视频封面为这些截图与介绍文字的排版。

要体验真实模型能力，继续阅读[五个大模型智能体示例](../../docs/MODEL_EXAMPLES.md)，使用自己的模型配置。
