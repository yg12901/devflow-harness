查看和维护 devflow 的自学习经验库。

## 看当前经验库

```bash
DF=.codebuddy/devflow/bin/devflow
$DF learnings                       # 全部（P0 铁律）
$DF learnings --stage S2            # 会注入到 S2 的那些
$DF learnings --tags cpp,build      # 按标签
```

输出里的 `prompt_block` 就是实际会被拼进 prompt 的文本。

## 手工加一条经验

```bash
$DF learn --stage S3 --type pitfall \
  --detail "<问题模式：什么情况下会出问题>" \
  --fix "<对策：下次该怎么做>" \
  --tags "cpp,lifecycle"
```

`--type`：`pitfall` 踩坑 / `best_practice` 好做法 / `gate_fail` 门禁问题 / `retry` 重试 / `rollback` 回退。

写 `--detail` 的要点：描述**模式**不是**实例**。"某文件第 78 行没判空"下次用不上；"这类限流器在配置未加载时会拿到空指针，新增实现时都要处理"才是可复用的。

## 蒸馏与分级

```bash
$DF distill --dry-run    # 先看会发生什么
$DF distill              # 真正应用
```

蒸馏做三件事：

1. **聚类** —— 按 signature（如 `gate_fail:S2:CHECK-1`）把事件归堆
2. **生成** —— 同一签名命中 2 次以上，自动生成候选经验条目
3. **升级** —— 跨 2 个 run 复现升 P1；跨 3 个 run 且跨 2 个阶段升 P0（变成每次必读的全局铁律）

## 有效性回收（这是闭环的关键一环）

经验不是只进不出。每次门禁跑完，引擎会自动归因：

- 这条经验注入了，但对应门禁**还是失败** → `fail_after_inject + 1`。累计 2 次降级并标记 `needs_rewrite`（说明这条经验没抓住问题本质，描述得改）；累计 4 次直接淘汰。
- 注入后对应门禁**连续 3 次没失败** → 标记 `proven`，固化下来不再参与降级。

所以经验库会自己收敛：有用的浮上去，没用的沉下去被淘汰，而不是越堆越大最后没人看。

查看当前哪些经验需要改写：

```bash
$DF reflect
```

## 手工编辑

`.codebuddy/devflow/learnings/active.yaml` 可以直接改。手工新增的条目把 `origin` 设成 `manual`，自动蒸馏不会覆盖它，也不会自动降级。

参数：$ARGUMENTS
