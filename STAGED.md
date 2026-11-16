# 两阶段设备累计量分析

这是现有归档的功能扩展。README.md 中的原业务、全部原接口、历史数据、原测试及数值域继续有效；本文件只定义新增能力。新增实现尚未提供。任务管理负责固定两段输入，连续分析负责消费固定输入并发布同一路径的联合报告，两者共同交付可复算的运行方案。

## 调用与身份

在现有 analysis_archive.Archive 增加：

- create_staged_task(request_id, stages, initial) -> 正整数 task_id
- get_staged_task(task_id) -> dict
- run_staged_task(task_id, *, fault=None) -> dict
- get_staged_result(task_id) -> dict

request_id、model_id 和所有目录身份沿用 README.md 的 ID 规则。stages 是恰含两项的 list 或 tuple；每项是三项 list 或 tuple：(model_id, revision, duration)。revision 是正整数或 None，None 表示创建时的当前修订；布尔值不是整数。各段 duration 沿用原验证规则，分别在 [0,8]，无需限制两段时长之和到 8。initial 是原状态 ID 到有理概率的字典，格式、缺省零和精确归一规则沿用原接口。

两段模型须具有相同的状态 ID 集合和指标 ID 集合，目录顺序可以不同；事件无需具有相同身份、数量、端点或速率。状态在边界按 ID 延续，指标总量按 ID 相加，不能按第二段目录的位置直接连接。输出目录以第一段固定修订的状态、指标顺序为准。

## 固定方案与重放

创建时在同一原子操作内固定两段实际修订、时长、初始分布以及第一段目录，任一参数或模型不合法时不产生新任务。执行只消费保存的方案。

新任务与旧 create_task 的请求命名空间独立，数值 task_id 可以重合，分别由各自 API 解释。新请求重放比较规范化的显式参数：阶段顺序、模型 ID、显式 revision、duration 的原验证结果，以及 initial 的有理值。list 与 tuple 等价；相等时长的不同合法写法等价；概率字典顺序、等值有理写法及省略零项等价。None 与显式修订不同。对合法形状的重放先比较已存请求绑定，再解析当前模型；相同参数返回原 task_id，即使模型当前指针已改变；不同参数抛 RequestConflict。非法形状、非法数值先抛 ValidationError。

get_staged_task 返回：

```python
{
    "task_id": 1, "request_id": "shift",
    "stages": [
        {"model_id": "operating", "revision": 1, "duration": 1.0},
        {"model_id": "maintenance", "revision": 1, "duration": 1.0}
    ],
    "parameters": {
        "states": ["on", "off"], "metrics": ["count", "signed"],
        "initial": {"on": "1", "off": "0"}
    },
    "status": "pending"
}
```

这是结构示例，不规定新任务起始编号。parameters.initial 包含第一段全部状态，值为最简有理数字符串；stages 中 revision 均为解析后的正整数，duration 均为浮点数。status 为 pending 或 succeeded。

模型更正、输入或返回值修改和关闭重开均不得改变固定方案及已发布报告。既有数据库直接可打开，旧模型、任务、结果及回执仍可按原接口使用；不要求给旧任务回填新报告。新增存储的内部组织自定。

## 同一路径的统计语义

第二阶段从第一阶段结束状态继续同一随机路径，边界无额外支付。每阶段累计量只包括本阶段的状态停留积分和事件向量支付。自事件仍随机支付，并行事件分别发生，同次事件对各指标的支付共享同一次发生；负奖励、吸收状态的停留奖励及随机初态均保留原语义。零时长阶段的所有累计量、方差及与其他阶段的协方差为零，状态不变。

get_staged_result 和 run_staged_task 返回同一报告字典，顶层恰含 task_id、request_id、stages、parameters、statistics、receipt；前四项与 get_staged_task 一致，不含 status。receipt 仍为去除 receipt 后的完整报告经原 validation.dumps 规范化、UTF-8 编码所得 SHA-256 小写十六进制串。

statistics 必须包含：

- states：第一段状态 ID 列表；probabilities：对应的第二段结束状态概率。
- metrics：第一段指标 ID 列表。
- coordinates：阶段优先的二元列表，先 [1, metrics[0]] 至 [1, metrics[-1]]，再第二阶段。阶段号从 1 开始。
- means：按 coordinates 排列的两段累计量均值。
- covariance：按同一目录排列的完整总体协方差方阵，包含段内及跨段块。
- total_means、total_covariance：按 metrics 排列的两阶段同指标之和的均值和完整协方差。

不能把前段累计量与边界状态视为独立。总量协方差必须包括两个跨段块，不能仅相加两个阶段的段内方差。概率、均值及协方差返回有限浮点数，沿用 abs(actual-expected) <= 2e-8 + 2e-8*abs(expected)，不得用模拟估计代替要求的数值结果。允许全量重算、重复调用现有数值接口或扩展内部计算，不规定算法，不要求改用 SDK2，不增加多工作者或性能优化要求。

## 发布、异常和兼容

结果、成功状态和回执共同提交；成功重跑返回原报告且不调用 fault。

新任务支持原三个故障点 after_compute、after_result_insert、before_commit，顺序及回滚语义与原任务一致；回调或数值异常原样传播，失败保持 pending 且无部分结果。未知模型、修订或任务抛 NotFound；非法输入、ID 集合不匹配或非法 task_id 抛 ValidationError；pending 的 get_staged_result 抛 ResultNotReady。不新增公开异常类型。读取结果和任务返回的嵌套值均与存储分离。

## 样例与核验

单指标手算例：第一段从 on 以速率 1 跳至 off 并支付 m=1，运行 1；第二段无事件，只在 off 按 m=1 的速率累计，运行 1；初始必为 on。令 p=1-exp(-1)，两段累计量相等，均值均为 p，跨段协方差为 p(1-p)，总量均值为 2p、方差为 4p(1-p)。

staged_examples.py 提供双指标、第二段目录重排和负奖励的完整可调用样例，并提供随机初态的解析预期。reference 模式仅展示输入及解析值，在未开发扩展的基线即可运行；check 模式在实现新 API 后核对公开样例；demo 模式创建固定方案、更正同名模型、注入提交前失败，关闭重开后执行旧方案并输出真实报告。后两种模式是新增能力的调用夹具，不包含目标统计实现。

交付说明须解释固定身份与阶段统计之间的交接不变量。保留全部原测试字节，补充独立核账，覆盖解析跨段依赖、目录重排及修订更正后的旧方案、随机初态、两侧分别为零时长和全零时长、自事件及同次多指标支付、并行负支付、三个故障点和真实落盘重开。同一模型拆为两段且总时长不超过 8 时，总量及最终概率须符合原单段分析；还须核对各段原允许数值域的边界。独立核账不能只让新实现与自身重复计算比较。

沿用现有 .pointwise-runtime.json 的运行格式，使 verify 同时执行完整原套件及新增核账，demo 保留原业务演示并展示更正后重开执行的两阶段真实报告。可新增组合入口，命令组织自定；setup 仍为准备命令的 argv 数组列表，verify/demo 各为一条命令的 argv 数组。无需依赖额外服务或私有数据。
