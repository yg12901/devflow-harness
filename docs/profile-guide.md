# Profile 指南：适配你自己部门的技术栈

面试官那句「每个部门每个人遇到的情况不一样」，在工程上的答案就是这一层。

引擎完全不认识 `cmake`、`gtest`、`git` 这些词。它只知道「需要构建的时候去
`profile.commands.build` 里取一条命令来执行」。所以换技术栈 = 换一个 YAML 文件，
引擎和七个角色定义一行都不用改。

---

## 最小改动：两行

```bash
cd .codebuddy/devflow/config/profiles
cp default.yaml myteam.yaml
```

改两处：

```yaml
name: myteam
description: 我们组的 Go 后台服务

commands:
  build: "go build ./..."
  test_unit: "go test ./... -count=1"
```

验证并使用：

```bash
devflow doctor --profile myteam           # 确认命令对应的可执行文件在本机存在
devflow init --profile myteam --title "..." --type feature
```

想设成默认：改 `config/workflow.yaml` 的 `defaults.profile`，
或者安装时 `./install.sh <target> --profile myteam`。

---

## 各字段说明

### `commands` —— 唯一的「怎么执行」来源

```yaml
commands:
  build: ""              # S3/S4/S5 门禁会独立重跑
  test_unit: ""          # S5 门禁会独立重跑
  test_integration: ""   # 可选
  lint: ""               # 可选
  static_check: ""       # 可选
  format_check: ""       # 可选
  coverage: ""           # 可选
  release_check: ""      # 可选，S6 发布预检
  clean: ""              # 可选
```

留空表示本 profile 不提供该能力。门禁按 `on_missing` 策略降级为 `WARN`
（记录并放行，但会反映在分数上），**不会假装检查过了**。

支持 `{{ }}` 占位符：`{{ run_id }}`、`{{ project_root }}`、`{{ artifacts_dir }}`、`{{ title }}`。

```yaml
commands:
  build: "bazel build //... --config={{ run_id }}"
```

### `doctor_commands`

`devflow doctor` 会检查这些命令的首个可执行文件是否真实存在，
避免跑到 S3 才发现本机没装构建工具。

```yaml
doctor_commands: [build, test_unit]
```

### `vcs` —— 版本控制约束

```yaml
vcs:
  kind: git
  allow: [git status, git diff, git add, git commit]
  deny:                      # 红线：hook 会直接拒绝执行
    - git push --force
    - git reset --hard
    - git checkout -b
  commit_template: "{{ run_id }}: {{ summary }}"
```

`deny` 列表会被 `.codebuddy/hooks/auto-approve.py` 读取并加进拦截规则，
和引擎内置的通用危险命令黑名单合并生效。

### `tracker` —— 需求管理系统

```yaml
tracker:
  kind: none               # none = 需求由用户在对话里直接给出
  mcp_tool: ""             # 接内部系统时填对应的 MCP 工具名
  comment_on_stages: []    # 需要回写评论的阶段，如 ["S1", "S6"]
  on_unavailable: degrade  # degrade = 拿不到就降级继续 | abort = 中止
```

默认 `none`。接内部需求系统时填上 MCP 工具名和需要回写的阶段。
建议保持 `on_unavailable: degrade` —— 外部系统不可用不该阻断本地开发流程。

### `release` —— 发布

```yaml
release:
  mr_platform: ""
  mr_create_command: ""
  pipeline_trigger_command: ""
  pipeline_status_command: ""
  require_rollback_plan: true
  dry_run: true            # 安全默认值：只生成发布单，不真正触发发布
```

`dry_run: true` 是刻意的安全默认。要让流水线真的发布，必须显式改成 `false`。

### `conventions` —— 工程约定

会原样注入各阶段 prompt，用来约束代码风格与设计习惯：

```yaml
conventions:
  - 新增源文件必须同步加入构建配置，否则不参与编译
  - 对外接口变更必须同时更新头文件注释与调用方
  - 错误处理走返回码而非异常
```

这里写的是「在我们组，代码应该长什么样」。写得越具体，产出越贴合团队习惯。

### `quality_gates` —— 质量红线

```yaml
quality_gates:
  max_files_per_task: 5          # 任务粒度上限，plan-check CHECK-7 使用
  require_test_for_new_logic: true
  forbid_commented_out_code: true
  forbid_todo_without_owner: true
```

`code-reviewer` 会直接引用这些作为评审依据。

---

## 几个技术栈的例子

### Go 服务

```yaml
name: go-service
description: Go 后台服务（go build + go test）
languages: [Go]
commands:
  build: "go build ./..."
  test_unit: "go test ./... -count=1"
  lint: "golangci-lint run"
  coverage: "go test ./... -coverprofile=coverage.out"
doctor_commands: [build, test_unit]
```

### Bazel / Blade 的 C++ 大仓

```yaml
name: cpp-bazel
description: C++ 服务（Bazel 构建）
languages: [C++]
commands:
  build: "bazel build //..."
  test_unit: "bazel test //... --test_output=errors"
  static_check: "bazel build //... --config=clang-tidy"
doctor_commands: [build]
conventions:
  - 新增 .cc 文件必须加入所属 BUILD 的 srcs
  - 跨模块依赖必须在 BUILD 的 deps 里显式声明
```

### Python 服务

```yaml
name: py-service
description: Python 服务（pytest + ruff）
languages: [Python]
commands:
  build: "python -m compileall -q ."
  test_unit: "python -m pytest -q"
  lint: "ruff check ."
  format_check: "ruff format --check ."
doctor_commands: [test_unit]
```

### Node / TypeScript

```yaml
name: node-ts
description: TypeScript 服务
languages: [TypeScript]
commands:
  build: "npm run build"
  test_unit: "npm test -- --run"
  lint: "npm run lint"
doctor_commands: [build, test_unit]
```

---

## 常见问题

**构建很慢，门禁每次都重跑受不了。**
`command_ok` 跑的是增量构建（`cmake --build` / `go build` 都是增量的），
通常只有几秒。如果确实慢，可以在 `workflow.yaml` 里把 S4 的 `command_ok` 去掉，
只保留 S3 和 S5 各一次。但不建议全部去掉 —— 那就退回到「相信 Agent 自述」了。

**我们组构建必须在容器/远端机器上跑。**
`commands.build` 是一条 shell 命令，写成 `docker run ... make` 或
`ssh buildbox 'cd /path && make'` 都可以。引擎只看退出码。

**多个仓库/多个模块怎么办。**
profile 里的命令在 `project_root()` 下执行。多模块场景可以写成
`make -C moduleA && make -C moduleB`，或者给每个模块各建一个 profile。

**能不能一个仓库用多个 profile。**
可以。`devflow init --profile <名字>` 是按 run 指定的，
同一个仓库里不同需求可以用不同 profile。profile 名字会记在 `run-state.json` 里，
后续 `next` / `gate` / `resume` 自动沿用。

**改了 profile 之后要重新 init 吗。**
不用。profile 是每次用到时现读的，改完立刻生效。但如果改的是 `name`
或者换了 profile 文件，已有的 run 仍会按 `run-state.json` 里记的名字去找。
