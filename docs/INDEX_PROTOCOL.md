# 无损整数索引协议

0.63 增加语言原生 `index(value) -> Int` 和 `__index__` 协议。可用普通类表示智能体产生的位置、序号或区间端点；执行仍经过当前解释器的能力、所有权、预算与取消边界，不使用宿主类型转换或固定文本识别。

## 自定义位置

```chaos example=index-protocol-basic
class Position {
    let value: Int = -1;
    fn __index__(self) -> Int { self.value }
}
let position = Position();
[index(position), [20, 22][position], (20, 22)[position], "AB"[position]]
```

结果为 `[-1,22,22,"B"]`。原生 Int 直接返回；实例通过其类型 C3 MRO 解析 `__index__`，遵循方法／描述符绑定。同名实例字段不覆盖特殊方法查找。

协议返回值必须是真正 Int。Bool、Number、Task、生成器和没有协议的值报 argument_error，不调用 `int`、`__int__` 或任何有损舍入。async／生成器方法不被隐式等待或消费。方法内部普通同步语言效果仍走原调度器，不能获得额外能力；它可以调用同步 Agent，但必须最终交付 Int。

## 切片与明确求值顺序

```chaos example=index-protocol-slice
class Step { fn __index__(self) -> Int { 2 } }
let spec = slice_key(null, null, Step());
[[10, 20, 30, 40, 50][::Step()], spec.step, slice_indices(spec, 5)]
```

结果为 `[[10,30,50],2,[0,5,2]]`。切片语法的 start／stop／step 表达式先依次求值，再按相同顺序各转换一次。`slice_key` 的参数表达式按书写顺序求值（具名实参可以重排），随后协议转换固定按 start→stop→step，各一次；非 null 端点转换为 Int；构造后的不可变 Slice 只保存 Int／null，不保存可再次执行的实例。`slice_indices` 的长度也采用无损协议，仍必须非负。步长 0 和既有整数预算检查保持不变。

用户 `__getitem__`／`__setitem__`／`__delitem__` 收到普通单项或 Tuple 键时保留其原值，不自动把任意业务键变成整数；直接切片组件仍转换为受控 Slice。Record 只接受 String 键。新方言的原生 List／Tuple／String 的 `[]` 和 `getitem` 接入协议，原生容器仍采用值式更新。

```chaos example=index-protocol-explicit
class Position { fn __index__(self) -> Int { 1 } }
let pos = Position();
[index(1), range(index(pos), 4), [10, 20, 30][pos]]
```

结果为 `[1,[1,2,3],20]`。`range`、`take/drop`、旧 `slice(value,start,stop)` 等保留原参数合同；需要转换时明确调用 index。历史方言 1 与显式 `object.[key]` 也保持旧成员／容器规则。

## 整数与条目协议的执行状态

`integer_index`、`resolve_index`、`invoke_item` 与 `operation` 的待决阶段由 IntegerIndexFrame、IndexResolveFrame、ItemInvokeFrame 和 ItemOperationFrame 驱动，键表达式和旧索引分别由 ItemKeyFrame 与 LegacyIndexFrame 驱动。协议方法和描述符通过同一访问／Invocation 帧进入，切片每个端点只转换一次；恢复不重算已经完成的目标、键、右值或效果。写入仍必须同步返回 null，普通读取仍可交付冷 Task／Generator，显式 `.[key]` 与方言1保留原规则。帧保存真实值并接受同一捕获图、所有权、能力和取消检查；这不增加公开捕获接口，不把整个语言或可复用 continuation 标为完成。

## 编译阶段与编辑器

纯宏阶段的原生 `[]` 只能计算原生 Int 索引，不执行实例协议或 Agent；`index(...)` 调用本身也不是纯编译期函数，须留在生成 AST 的运行阶段。生成的 AST 可在运行阶段调用 index；这遵循已有 quote／卫生宏合同。协议能力由共享 NativeSpec 驱动签名、IDE／LSP 提示、AgentTool、SDK、HTTP 与 CLI，格式化和结构编辑不执行协议。更完整的索引读写和历史语法见[索引指南](INDEXING.md)。

本设计参考[Python 无损索引协议 PEP 357](https://peps.python.org/pep-0357/)与[特殊方法查找](https://docs.python.org/3/reference/datamodel.html#special-method-lookup)，在 Chaos 的严格 Bool、不可变 Slice、值式容器及效果边界下实现。它不代表支持 Python 的全部对象协议。
