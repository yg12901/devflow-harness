# devflow

**把一条需求从「提出来」自动跑到「上线归档」的多 Agent 流水线，跑在 CodeBuddy 里。**

七个角色分工协作，每个阶段过脚本化门禁，失败自己重试，中断能从断点恢复，
踩过的坑会沉淀成经验注入下一次运行。

---

## 它解决什么

用 AI 写代码，真正卡人的从来不是「生成不出代码」，而是这四件事：

| 卡点 | devflow 的做法 |
|---|---|
| **跑到一半断了，靠人来说「继续」** | 状态收敛到单一真相源；subagent 静默退出时回头看产物判断真实进度，自动接管而不是等人 |
| **不知道该去哪写代码，靠人维护业务上下文** | 启动前先扫仓库生成地图（模块/热点/构建体系），架构阶段强制带 `文件:行号` 证据做依赖分析 |
| **每个部门技术栈不一样，流程没法复用** | 引擎完全不认识任何技术栈，构建/测试/发布命令全在 profile 里，换部门只改一个 YAML |
| **同样的错反复犯** | 门禁失败自动落事件 → 按签名聚类蒸馏 → 跨需求复现自动升级 → 注入下一轮 prompt；**注入后仍失败的经验会被降级淘汰** |

---

## 30 秒看它跑起来

```bash
git clone <本仓库> && cd devflow-harness
./examples/run-demo.sh /tmp/devflow-demo
```

演示完全离线，不需要大模型。它会把 devflow 装进一个真实的 C++ 工程，
然后驱动引擎跑完七个阶段，其中你会看到：

- **门禁真的会拦** —— S2 架构师漏了一个需求还声称自己没漏，`plan-check` 用集合运算直接戳穿并打回
- **门禁独立复算** —— S3/S5 的门禁自己跑 `cmake` 和 `ctest`，不看产物里写的「构建通过」
- **中断能恢复** —— 模拟一次 subagent 静默死亡，引擎靠产物判定真实进度
- **学习闭环** —— 第二条需求踩同一个坑，经验自动沉淀并从 P2 升到 P1

> 演示里各阶段的产物来自预置文件，不是模型现场生成的。
> 但引擎、门禁、状态机、学习闭环这四样是同一套代码 —— 在 CodeBuddy 里跑 `/flow` 时，
> 只是把「读预置文件」换成「调用真实 Agent」，其余路径完全一致。

---

## 装到你自己的仓库

```bash
./install.sh /path/to/your-repo
cd /path/to/your-repo

.codebuddy/devflow/bin/devflow doctor      # 确认 profile 的命令在本机可用
.codebuddy/devflow/bin/devflow repo-map    # 生成仓库地图
```

然后在 CodeBuddy 里：

```
/flow 给限流器加上按用户维度的配额支持
```

技术栈不是 C/C++ 的话，先配一个自己的 profile（通常只用改两行）：

```bash
cd .codebuddy/devflow/config/profiles
cp default.yaml myteam.yaml       # 改 commands.build 和 commands.test_unit
devflow doctor --profile myteam
```

详见 [docs/profile-guide.md](docs/profile-guide.md)。

---

## 七个角色与七个阶段

| 阶段 | 角色 | 产出 | 门禁重点 |
|---|---|---|---|
| S1 需求分析 | `requirement-analyst` | 带编号需求点 + 知识库 | 需求编号、疑点标注、影响范围有代码证据 |
| S2 方案与拆分 | `architect` | 方案 + 依赖地图 + 任务清单 + 影响报告 | **9 项结构校验**（覆盖率、孤立任务、依赖成环……） |
| S3 编码 | `developer` | 代码 + 变更记录 | 任务全部完成、影响范围逐条确认、**独立重跑构建** |
| S4 评审闭环 | `code-reviewer` ↔ `developer` | 问题清单 + 逐条决策 | CR 编号、明确定级、必修项不得推迟 |
| S5 测试 | `test-engineer` | 测试报告 | 验收标准对照、**独立重跑测试** |
| S6 发布 | `developer` | 发布单 | **回滚方案不得为空** |
| S7 知识沉淀 | `knowledge-engineer` | 经验入库 + 文档更新 | 经验真正写进库，不是只写在文档里 |

`leader` 是第七个角色，负责编排：调度、跑门禁、裁决推进，自己不产出任何阶段产物。
S2 任务拆分的覆盖率、孤立任务、依赖成环等由脚本校验；文档与经验由 `knowledge-engineer` 在流程末端一次写完。

---

## 常用命令

```bash
DF=.codebuddy/devflow/bin/devflow

$DF doctor                       # 环境自检
$DF repo-map                     # 生成仓库地图
$DF init --title "..." --type feature
$DF next                         # 取下一阶段 prompt（已装配上游摘要+经验+命令）
$DF gate --stage S2              # 跑门禁（0=通过 1=打回 2=执行错误）
$DF stage-update --stage S2 --status passed --summary "..." --score 96
$DF status                       # 当前进度
$DF resume                       # 中断后恢复
$DF inspect --stage S3           # subagent 没返回时，看产物真实进度
$DF rollback --to S2 --reason "方案不可行"
$DF learnings --stage S2         # 看会注入到某阶段的经验
$DF distill                      # 蒸馏事件为经验并重算优先级
$DF reflect                      # 运行结束复盘
```

斜杠命令：`/flow`、`/flow-status`、`/flow-resume`、`/flow-rollback`、`/flow-learn`。

实时看板：

```bash
python3 .codebuddy/devflow/dashboard/serve.py    # http://127.0.0.1:8770
```

---

## 目录结构

```
.codebuddy/
├── agents/            7 个角色定义（CodeBuddy 自动发现）
├── skills/            按需加载的配方（需求 / 探索 / 影响 / 拆分 / 实现 / 评审 / 测试 / 发布 / 复盘）
├── commands/          /flow 系列斜杠命令
├── hooks/             命令自动放行（黑名单模式，避免卡在人工确认）
├── settings.json
└── devflow/
    ├── bin/devflow    CLI 入口
    ├── engine/        L1 领域无关内核（约 2000 行）
    │   ├── core.py        路径、配置、profile、命令执行
    │   ├── state.py       运行态单一真相源
    │   ├── gates.py       门禁原语 + 9 项计划结构校验
    │   ├── learning.py    自学习闭环
    │   ├── repomap.py     仓库地图预热
    │   ├── runner.py      编排：装配 prompt、跑门禁、推进与恢复
    │   └── cli.py         命令行
    ├── config/
    │   ├── workflow.yaml  L3 流水线定义（阶段 + 声明式门禁）
    │   └── profiles/      L2 技术栈适配
    ├── prompts/       七个阶段的 prompt 模板
    ├── learnings/     经验库 + 事件流水
    ├── dashboard/     实时看板
    └── runs/          每次运行的状态与产物
```

三层分离是这套东西能跨部门复用的关键：
**L1 引擎不认识任何技术栈，L2 profile 装「本部门怎么干活」，L3 workflow 装「流程长什么样」。**

---

## 两个设计选择

**门禁是配置，不是代码。** 引擎只实现约 10 个通用校验原语
（`file_exists` / `sections_present` / `yaml_path_empty` / `command_ok` …），
具体校验什么写在 `workflow.yaml` 里。加一道门禁是加几行 YAML，不用碰 Python。

**门禁独立复算，不采信 Agent 自述。** 校验「构建是否通过」的方式不是在 markdown 里
grep `BUILD SUCCESSFUL` —— 那行字恰恰是 LLM 自己写的，等于让被考核者填自己的成绩单。
`command_ok` 原语会把 profile 里的构建/测试命令真正重跑一遍，用退出码说话。

---

## 文档

- [docs/architecture.md](docs/architecture.md) —— 架构与关键设计决策
- [docs/profile-guide.md](docs/profile-guide.md) —— 怎么适配自己部门的技术栈
- [docs/self-learning.md](docs/self-learning.md) —— 自学习闭环的完整机制

## 环境要求

Python 3.6+（只用标准库 + PyYAML）。构建与测试工具由 profile 决定，引擎本身不依赖。
