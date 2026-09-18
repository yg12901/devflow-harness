启动 devflow 完整流水线，把一条需求从分析一路推到上线归档。

## 解析参数

从 `$ARGUMENTS` 里解析：

| 参数 | 格式 | 默认 | 说明 |
|---|---|---|---|
| 需求描述 | 自然语言 | **必填** | 需求原文，可以很长 |
| 类型 | `feature` / `bugfix` / `hotfix` / `refactor` | `feature` | 决定跳过哪些阶段 |
| profile | `--profile <名字>` | workflow.yaml 里的默认值 | 技术栈适配 |

例子：
- `/flow 给限流器加上按用户维度的配额支持`
- `/flow 修复配置热更新后旧值未失效的问题 bugfix`
- `/flow 接口层重构 refactor --profile myteam`

`$ARGUMENTS` 为空时，**主动问用户要需求描述**，不要猜。

---

## 执行

你现在的身份是 **leader**。读 `.codebuddy/agents/leader.md` 拿到完整编排协议，然后按下面的顺序走。

### 1. 环境自检

```bash
DF=.codebuddy/devflow/bin/devflow
$DF doctor
```

有 FAIL 就先报告给用户，别硬跑。常见问题：
- profile 里的构建命令在本机不存在 → 提示用户换 profile 或改命令
- 仓库地图未生成 → 下一步会生成，不算问题

### 2. 生成仓库地图（首次）

```bash
[ -f .codebuddy/devflow/repo-map.md ] || $DF repo-map
```

这一步解决的是「Agent 不知道去哪写代码」：提前扫出模块分布、热点文件、构建体系，后续各阶段按需精准加载，不用每次从零 grep 全仓。

### 3. 初始化

```bash
$DF init --title "<从需求里提炼的标题>" --type <类型> --requirement "<需求原文>"
```

记下返回的 `run_id`，告诉用户：

```
✅ devflow 已启动
   需求：<标题>
   编号：<run_id>
   类型：<类型>（跳过阶段：<skipped_stages>）
   技术栈：<profile>

   流水线：S1 需求分析 → S2 方案与拆分 → S3 编码 → S4 评审 → S5 测试 → S6 发布 → S7 沉淀
   进度我会逐阶段汇报，中途需要你决策时会停下来问。
```

### 4. 主循环

```
循环直到所有阶段完成：
  $DF next                              # 拿 stage / role / max_turns / prompt
  task(subagent_name=<role>,
       prompt=<上一步返回的 prompt 字段，原样透传>,
       description="<stage> <name>")
  解析返回的 JSON
  $DF gate --stage <stage>
  退出码 0 → $DF stage-update --stage <stage> --status passed --summary "<摘要>" --score <score>
             汇报一行，立刻进入下一阶段
  退出码 1 → 见 leader.md 的「异常处理」
```

**prompt 原样透传**：`$DF next` 返回的 prompt 已经装配好了上游摘要、本阶段匹配到的历史经验、仓库地图提示和 profile 的真实命令。不要改写它。

S4 阶段需要在内部来回调度 code-reviewer 和 developer，最多 3 轮，详见 leader.md。

### 5. 收尾

```bash
$DF reflect
```

向用户汇报：各阶段耗时与评分、代码改动范围、测试结果、发布状态、本轮新沉淀的经验条数。

---

## 什么时候可以停下来问人

只有这几种情况：门禁连续 3 次不通过、构建或测试反复失败、需求本身有歧义、发布前的最终确认。

其他时候**一路推到底**，不要在阶段之间输出"是否继续？"。

---

用户的需求：$ARGUMENTS
