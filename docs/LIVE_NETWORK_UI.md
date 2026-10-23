# 从语言生成实时网络页面

`NetworkGraph` 和 `RuntimeGraph` 是共享 OpenUI 组件目录中的普通组件，继续使用原生 `ui_node`、`ui_artifact`、`ui_from_openui` 和 `ui_generate`。函数、源码、IR、SDK、CLI 与页面不另建执行模型。

## 自动展示真实状态

```chaos example=runtime-network-cold
let view = ui_artifact("page", "系统状态", ui_node("RuntimeGraph", ["运行网络", "radial", "project", "auto"]));
type_of(view)
```

把最后一行换成 `view` 并运行，会在“生成产物”中显示真实网络。选择“固定实时视图”可在查看其他运行时保留页面。组件通过现有项目事件流收到变更通知，再读取同一时点的有界完整快照。它不会发起任务、调用模型或修改程序。

`scope="run"` 显示产物所来自运行的冻结源版本；`scope="project"` 显示该项目当前结构与有界运行事实。产物只能绑定宿主提供的当前项目及实际运行身份；组件属性不能指定 URL、其他项目或其他运行。本地候选、未提交的生成预览没有实时绑定。

图中区分源码声明关系和实际观测事实。没有事件的函数不标为已执行；嵌套 Program 的任务、调用与同名节点按真实调用身份隔离。节点详情显示状态及可用证据位置。未观测、断连、历史窗口截断都显式显示。动画不制造任务活动或隐藏推理。

当前快照最多读取最近500条事件，显示400节点、1200关系；源码语义节点最多160。计数是已显示窗口，不是无限历史或完整系统证明。跨版本历史运行读取当时的程序快照；代码字面量、模型提示、工具参数、运行输入输出和原始错误不投影到网络。

## 普通函数生成自己的网络

```chaos
fn render(nodes, edges) {
  ui_artifact("report", "依赖分析", ui_node("NetworkGraph", ["计算关系", nodes, edges, "radial", "dark"]))
};
render([{id:"planner",label:"规划"},{id:"worker",label:"执行"}], [{id:"delegates",source:"planner",target:"worker"}])
```

节点与关系均为闭合纯 JSON。节点需要唯一 `id`、`label`，关系需要唯一 `id`、存在的 `source` 和 `target`；可提供种类、状态和说明。静态组件限制200节点、400关系，超限拒绝；不从文字猜测关系。布局有 `radial`、`layered`，主题有 `auto`、`dark`、`light`。支持平移缩放、键盘选择、邻域高亮、搜索和状态卡片。

智能体可用 `ui_prompt()` 获取同一目录，再经 `ui_generate` 生成包含这些组件的完整页面。模型定义展示结构，状态事实由宿主事件与记录提供；OpenUI不执行任意 JavaScript、Query 或 Mutation。

## SDK、命令行与导出

Python 使用 `Framework.runtime_view(project_id, run_id=...)` 或 `Client.runtime_view(...)`；只读接口为 `GET /api/v1/projects/{projectId}/runtime-view`，可选 `runId`。终端使用 `chaos ui runtime PROJECT --server http://127.0.0.1:8891 --run-id RUN`。

通用网络可导出为确定性的离线 SVG/HTML。RuntimeGraph 离线导出明确显示“未连接运行宿主”，不伪装实时页面；HTML仍禁止网络连接。旧14组件库的已有产物继续保留原始 source/hash 并可验证、预览、导出；把其源码重新编译或导出为 `.chaos` 后运行，会按当前扩展组件目录生成新产物哈希，属于重新编译。
