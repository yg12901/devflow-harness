#!/usr/bin/env bash
# ============================================================
# devflow 端到端演示 —— 完全离线，不需要任何大模型
# ============================================================
#
#   ./examples/run-demo.sh [工作目录]
#
# 这个脚本把 devflow 装进一个真实的 C++ 工程，然后驱动引擎跑完七个阶段。
#
# 【这个演示证明什么】
#   1. 门禁是真的会拦 —— S2 第一版任务拆分漏了一个需求，plan-check 直接算出来并打回
#   2. 门禁独立复算 —— S3/S5 的门禁自己跑 cmake 和 ctest，不看产物里写的"构建通过"
#   3. 自学习是闭环 —— 门禁失败自动落事件，蒸馏成经验，注入下一次运行的 prompt
#   4. 中断能恢复 —— 演示中途模拟一次 subagent 静默死亡，引擎靠产物判断真实进度
#
# 【这个演示不证明什么】
#   各阶段的产物内容来自 fixtures/ 下的预置文件，不是模型现场生成的。
#   在 CodeBuddy 里跑 /flow 时，这些产物由七个角色 Agent 实际产出，
#   而引擎、门禁、状态机、学习闭环这四样东西是同一套，跑的就是这里演示的代码路径。
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
DEMO_SRC="$SCRIPT_DIR/demo-ratelimit"
FIX="$DEMO_SRC/fixtures"

WORK="${1:-/tmp/devflow-demo}"

BOLD='\033[1m'; GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'
BLUE='\033[0;36m'; DIM='\033[2m'; NC='\033[0m'

step()  { printf "\n${BOLD}${BLUE}━━ %s${NC}\n" "$1"; }
note()  { printf "${DIM}   %s${NC}\n" "$1"; }
ok()    { printf "   ${GREEN}✓${NC} %s\n" "$1"; }
bad()   { printf "   ${RED}✗${NC} %s\n" "$1"; }
warn()  { printf "   ${YELLOW}!${NC} %s\n" "$1"; }

# ---------- 准备工作目录 ----------
step "准备演示工作目录"
if [ -e "$WORK" ]; then
  printf "   目录已存在：%s\n" "$WORK"
  printf "   演示需要一个干净目录。删除它并继续？[y/N] "
  read -r reply
  case "$reply" in
    [yY]*) rm -rf "$WORK" ;;
    *) printf "   已取消。换个目录：./examples/run-demo.sh /tmp/其他名字\n"; exit 1 ;;
  esac
fi
mkdir -p "$WORK"
cp -r "$DEMO_SRC/." "$WORK/"
rm -rf "$WORK/fixtures"
(cd "$WORK" && git init -q && git add -A && git -c user.email=demo@local -c user.name=demo commit -qm "初始版本：仅支持全局限流")
ok "演示工程已就绪（一个 C++ 令牌桶限流库，含 3 条已有测试）"
note "$WORK"

# ---------- 安装 devflow ----------
step "把 devflow 安装进这个工程"
bash "$REPO_ROOT/install.sh" "$WORK" >/dev/null
ok "已安装到 $WORK/.codebuddy/"

cd "$WORK"
DF=".codebuddy/devflow/bin/devflow"
ART=""

jq_get() { python3 -c "import json,sys; d=json.load(sys.stdin); print(d$1)" 2>/dev/null || echo ""; }

# ---------- 环境自检 ----------
step "环境自检：devflow doctor"
$DF doctor >/dev/null 2>&1 || true
note "首次运行时「仓库地图未生成」会 FAIL，下一步就生成它"
$DF repo-map | python3 -c "
import json,sys
d = json.load(sys.stdin)
print('   ✓ 仓库地图已生成')
print('     语言：%s' % ', '.join(d['languages']))
print('     构建体系：%s' % ', '.join(d['build_systems']))
print('     模块：%s' % ', '.join(d['modules']))
"
$DF doctor >/dev/null && ok "doctor 全部 PASS"

# ---------- 初始化 ----------
step "初始化流水线"
RUN_ID=$($DF init \
  --run-id DEMO-01 \
  --title "限流器支持按用户维度限流" \
  --type feature \
  --requirement "同一个接口不同用户之间不应该互相挤占配额；运维希望能看到某个用户当前还剩多少额度" \
  | jq_get "['run_id']")
ART=".codebuddy/devflow/runs/$RUN_ID/artifacts"
ok "run_id = $RUN_ID"
note "产物目录：$ART"

# 统一的阶段推进函数
gate_and_advance() {   # $1=阶段 $2=摘要
  local stage="$1" summary="$2"
  if $DF gate --stage "$stage" >/tmp/df-gate.json 2>/tmp/df-gate.err; then
    local score
    score=$(python3 -c "import json;print(json.load(open('/tmp/df-gate.json'))['score'])")
    ok "门禁通过（${score} 分，$(python3 -c "import json;d=json.load(open('/tmp/df-gate.json'));print('%d 项检查' % d['total'])")）"
    $DF stage-update --stage "$stage" --status passed --summary "$summary" --score "$score" >/dev/null
    return 0
  fi
  return 1
}

show_prompt_injection() {  # $1=阶段
  $DF next --peek | python3 -c "
import json,sys
d = json.load(sys.stdin)
ids = d.get('injected_learnings') or []
print('   ↳ 本阶段自动注入 %d 条历史经验：%s' % (len(ids), ', '.join(ids) if ids else '无'))
print('     prompt 长度 %d 字符（已含上游摘要、经验、仓库地图、profile 命令）' % len(d.get('prompt','')))
"
}

# ============================================================
# S1 需求分析
# ============================================================
step "S1 需求分析"
show_prompt_injection S1
$DF next >/dev/null
cp "$FIX/s1/01-requirement.md" "$FIX/s1/knowledge-base.md" "$ART/"
note "requirement-analyst 产出：01-requirement.md + knowledge-base.md"
gate_and_advance S1 "拆出 R-01 按用户限流、R-02 查询剩余令牌；影响 3 个文件；2 项待澄清（用户数上限、Tick 驱动方式）" \
  || { bad "S1 门禁未通过"; cat /tmp/df-gate.err; exit 1; }

# ============================================================
# S2 —— 演示重点一：门禁真的会拦
# ============================================================
step "S2 技术方案与任务拆分 · 第 1 次尝试（架构师漏了一个需求）"
show_prompt_injection S2
$DF next >/dev/null
cp "$FIX/s2-good/02-design.md" "$FIX/s2-good/dependency-map.yaml" "$FIX/s2-good/impact-report.md" "$ART/"
cp "$FIX/s2-bad/tasks.yaml" "$ART/tasks.yaml"
note "architect 产出了方案，但 tasks.yaml 只拆了 R-01，漏了 R-02"
note "而它自己填的 coverage_matrix.uncovered_requirements 却写着 []（自查通过了）"

if gate_and_advance S2 "不应该通过"; then
  bad "门禁没拦住，演示失败"
  exit 1
fi
bad "门禁打回"
python3 -c "
import json
d = json.load(open('/tmp/df-gate.json'))
for c in d['blocking']:
    print('     %s' % c['check'])
    print('       证据：%s' % c['evidence'])
    if c.get('message'):
        print('       说明：%s' % c['message'])
"
note "这就是把结构性检查交给脚本而不是 LLM 的价值："
note "模型可以一边漏掉覆盖，一边在矩阵里声称自己没漏；集合运算不会。"

printf "\n"
python3 -c "
import json,glob,os
files = sorted(glob.glob('.codebuddy/devflow/learnings/events/*.jsonl'))
events = [json.loads(l) for f in files for l in open(f, encoding='utf-8') if l.strip()]
s2 = [e for e in events if e['stage'] == 'S2']
print('   ↳ 门禁失败已自动记录 %d 条学习事件（不依赖 Agent 自觉上报）' % len(s2))
for e in s2[:2]:
    print('     signature = %s' % e['signature'])
"

step "S2 · 第 2 次尝试（补上 R-02 的承接任务）"
cp "$FIX/s2-good/tasks.yaml" "$ART/tasks.yaml"
note "architect 按门禁反馈补了 T2，覆盖率矩阵补上 R-02 → T2"
gate_and_advance S2 "方案选组合而非改造 TokenBucket；拆 3 个任务（T1 限流器 / T2 查询接口 / TQ 收尾）；中风险 2 项（内存增长、Tick 开销）" \
  || { bad "第二次仍未通过，演示失败"; exit 1; }

# ============================================================
# S3 —— 演示重点二：门禁独立跑构建
# ============================================================
step "S3 编码实现"
show_prompt_injection S3
$DF next >/dev/null
cp -r "$FIX/s3/code/." "$WORK/"
cp "$FIX/s3/03-changes.md" "$ART/"
python3 - "$ART/tasks.yaml" <<'PY'
import re, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    text = f.read()
# developer 每做完一条任务就把 status 改成 completed —— 这是断点续跑的依据
with open(path, "w", encoding="utf-8") as f:
    f.write(text.replace("status: pending", "status: completed"))
PY
note "developer 新增 user_rate_limiter.{h,cpp}，改 CMakeLists.txt，逐条置 tasks 为 completed"
note "门禁接下来会自己跑一遍 cmake —— 不看 03-changes.md 里写的「构建成功」"
gate_and_advance S3 "新增 UserRateLimiter 组合 TokenBucket，3 个任务全部完成，构建通过，影响范围 6 项全部确认" \
  || { bad "S3 门禁未通过"; cat /tmp/df-gate.err; exit 1; }
python3 -c "
import json
d = json.load(open('/tmp/df-gate.json'))
for c in d['checks']:
    if c['check'].startswith('command_ok'):
        print('   ↳ 独立复算：%s' % c['evidence'][:120])
"

# ============================================================
# S4 代码评审闭环
# ============================================================
step "S4 代码评审闭环"
show_prompt_injection S4
$DF next >/dev/null
cp "$FIX/s4/04-review.md" "$FIX/s4/04-review-response.md" "$ART/"
note "code-reviewer 提 3 条（CR-01/02/03），developer 逐条决策：拒绝 / 采纳 / 后续迭代"
gate_and_advance S4 "3 条评审意见全部处置：CR-01 拒绝并在代码留注释说明前提，CR-02 采纳补注释，CR-03 后续迭代；定级可合并" \
  || { bad "S4 门禁未通过"; cat /tmp/df-gate.err; exit 1; }

# ============================================================
# 演示重点四：模拟 subagent 静默死亡
# ============================================================
step "模拟中断：S5 的 subagent 没有正常返回"
$DF next >/dev/null
cp -r "$FIX/s5/code/." "$WORK/"
warn "假设此时 subagent 耗尽 turn 静默退出，没有返回任何 JSON"
note "传统做法：流程卡住，等人来说「继续」"
note "devflow 的做法：回头看产物的真实进度再决定"
$DF inspect --stage S5 | python3 -c "
import json,sys
d = json.load(sys.stdin)
print('   ↳ devflow inspect --stage S5')
print('     判定：%s' % d['verdict'])
print('     已有产物：%s' % (d['present'] or '无'))
print('     缺失产物：%s' % (d['missing'] or '无'))
print('     建议：%s' % d['recommendation'])
"
note "判定为 empty —— 测试代码写了但报告还没产出，按建议重新调用补齐"

# ============================================================
# S5 —— 演示重点二续：门禁独立跑测试
# ============================================================
step "S5 测试验证（恢复后重新执行）"
cp "$FIX/s5/05-test-report.md" "$ART/"
note "test-engineer 新增 6 条用例，含守护「查询无副作用」约束的专项用例"
note "门禁接下来会自己跑一遍 ctest"
gate_and_advance S5 "新增 6 条用例覆盖 R-01/R-02，加已有 3 条回归全部通过；并发与内存增长为有意识不覆盖" \
  || { bad "S5 门禁未通过"; cat /tmp/df-gate.err; exit 1; }
python3 -c "
import json
d = json.load(open('/tmp/df-gate.json'))
for c in d['checks']:
    if c['check'].startswith('command_ok'):
        print('   ↳ 独立复算：%s' % c['evidence'][:150])
"

# ============================================================
# S6 发布
# ============================================================
step "S6 发布"
$DF next >/dev/null
cp "$FIX/s6/06-release.md" "$ART/"
note "developer 产出发布单；dry_run=true 只生成不真发布"
note "门禁会专门检查「回滚方案」不为空 —— 这是发布的最后一道保险"
gate_and_advance S6 "纯增量变更随调用方下次发版生效，无需灰度；回滚提供代码 revert 与接入方降级两种方式" \
  || { bad "S6 门禁未通过"; cat /tmp/df-gate.err; exit 1; }
note "分数不满是因为 default profile 没配 release_check 命令，该项记为 WARN 而非 FAIL —"
note "能力缺失就如实降级放行，不假装检查过了。配了发布预检命令后这项自然会变绿。"

# ============================================================
# S7 知识沉淀 —— 演示重点三：经验真的入库
# ============================================================
step "S7 知识沉淀"
$DF next >/dev/null
cp "$FIX/s7/07-knowledge.md" "$FIX/s7/knowledge-base.md" "$ART/"

note "knowledge-engineer 把本轮经验真正写进经验库（不是只写在文档里）"
$DF learn --stage S3 --type pitfall \
  --detail "按 key 分桶的组件，查询接口用 operator[] 会隐式创建元素，让只读操作改变状态并撑大 map" \
  --fix "查询接口声明为 const，未命中走显式只读分支；并单独立测试用例守住这条约束" \
  --tags "cpp,map,const,api" >/dev/null
$DF learn --stage S2 --type best_practice \
  --detail "需求是在现有能力上加一个维度时，改造现有类会破坏所有调用方并混淆职责" \
  --fix "新增类组合现有类作为单元，现有代码零改动，回滚可降级为接入方改一行" \
  --tags "design,composition,rollback" >/dev/null
$DF learn --stage S4 --type best_practice \
  --detail "拒绝评审建议的理由若依赖某个会变的前提，只写在响应文档里半年后没人会翻" \
  --fix "在代码里加注释写明前提，让后来者知道何时该重新评估" \
  --tags "review,comment,maintenance" >/dev/null
ok "3 条经验已入库"

gate_and_advance S7 "沉淀 3 条可复用经验；根因分析指向「结构性检查不能交给 LLM 自查」；知识库补充设计权衡记录" \
  || { bad "S7 门禁未通过"; cat /tmp/df-gate.err; exit 1; }

# ============================================================
# 复盘
# ============================================================
step "全流程复盘：devflow reflect"
$DF reflect | python3 -c "
import json,sys
d = json.load(sys.stdin)
lib = d['library']
dis = d['distilled']
print('   本轮学习事件：%d 条' % d['events_this_run'])
print('   按阶段分布：  %s' % d['events_by_stage'])
print()
print('   蒸馏结果：')
print('     扫描事件 %d 条，聚成 %d 个签名簇' % (dis['events_scanned'], dis['clusters']))
print('     新生成经验：%s' % (dis['created'] or '无'))
for p in dis['promoted']:
    print('     优先级变更：%s  %s -> %s（%s）' % (p['id'], p['from'], p['to'], p['reason']))
print()
print('   经验库现状：共 %d 条（P0 %d / P1 %d / P2 %d），已验证 %d 条，待改写 %d 条'
      % (lib['total'], lib['by_priority']['P0'], lib['by_priority']['P1'],
         lib['by_priority']['P2'], lib['proven'], lib['needs_rewrite']))
"

# ============================================================
# 演示重点三：自学习闭环 —— 同一个坑第二次踩到时会被沉淀并升级
# ============================================================
step "第二条需求：同一个坑再踩一次，看经验库怎么反应"
note "新起一条需求，架构师又一次漏掉了需求覆盖（同样的 CHECK-1）"
$DF init --run-id DEMO-02 --title "限流器支持配额热更新" --type feature \
  --requirement "希望不重启进程就能调整某个用户的配额上限" >/dev/null
ART2=".codebuddy/devflow/runs/DEMO-02/artifacts"
cp "$FIX/s1/01-requirement.md" "$FIX/s1/knowledge-base.md" "$ART2/"
$DF next --run-id DEMO-02 >/dev/null
$DF gate --run-id DEMO-02 --stage S1 >/dev/null 2>&1
$DF stage-update --run-id DEMO-02 --stage S1 --status passed --summary "沿用演示需求" --score 100 >/dev/null
cp "$FIX/s2-good/02-design.md" "$FIX/s2-good/dependency-map.yaml" "$FIX/s2-good/impact-report.md" "$ART2/"
cp "$FIX/s2-bad/tasks.yaml" "$ART2/tasks.yaml"
$DF next --run-id DEMO-02 >/dev/null
$DF gate --run-id DEMO-02 --stage S2 >/dev/null 2>&1 || true
bad "S2 门禁再次因 CHECK-1 打回（同一个签名，第 2 次出现）"

printf "\n"
note "现在跑蒸馏，看系统怎么处理这个重复出现的问题："
$DF distill | python3 -c "
import json,sys
d = json.load(sys.stdin)
print('   ↳ devflow distill')
print('     扫描 %d 条事件，聚成 %d 个签名簇' % (d['events_scanned'], d['clusters']))
if d['created']:
    print('     ✓ 新沉淀经验：%s' % ', '.join(d['created']))
for p in d['promoted']:
    print('     ✓ 优先级提升：%s  %s → %s' % (p['id'], p['from'], p['to']))
    print('       依据：%s' % p['reason'])
"

printf "\n"
note "这条新经验从下一次运行开始就会被自动注入 S2 的 prompt："
$DF learnings --stage S2 | python3 -c "
import json,sys
d = json.load(sys.stdin)
for e in d['entries']:
    if e['priority'] != 'P0':
        print('     [%s|%s] %s' % (e['id'], e['priority'], e['pattern'][:70]))
        print('              对策：%s' % (e['fix'] or '（待 knowledge-engineer 补充）')[:70])
        print('              统计：命中 %d 次，跨 %d 个需求' % (e['stats']['hits'], e['stats']['runs']))
"
note "闭环走完：门禁失败 → 自动采集 → 按签名聚类 → 跨 run 复现升级 → 注入下轮 prompt"
note "反向也成立：注入后同一门禁仍失败会降级并标记待改写，连续有效则固化为已验证。"

step "最终状态"
$DF status --run-id DEMO-01 | python3 -c "
import json,sys
d = json.load(sys.stdin)
icon = {'passed':'✓','failed':'✗','pending':'·','skipped':'-','running':'▶','blocked':'!'}
print('   %s · %s' % (d['run_id'], d['title']))
print('   状态 %s · 进度 %d%%' % (d['status'], d['progress']))
print()
for s in d['stages']:
    retry = ('  重试 %d 次' % (s['attempts']-1)) if s['attempts'] > 1 else ''
    score = ('%3d 分' % s['gate_score']) if s['gate_score'] is not None else '     '
    print('     %s %-3s %-12s %s%s' % (icon.get(s['status'],'?'), s['id'], s['name'], score, retry))
"

step "演示完成"
cat <<EOF
   演示工程与全部产物保留在：
     $WORK

   可以自己翻一翻：
     cd $WORK
     $DF status                                  # 流水线状态
     $DF learnings --stage S2                    # 看会注入到 S2 的经验
     cat .codebuddy/devflow/learnings/active.yaml # 经验库全文
     ls $ART                                     # 七个阶段的全部产物
     cat .codebuddy/devflow/repo-map.md          # 仓库地图

   刚才这一轮里，引擎真实做了这些事：
     · plan-check 用集合运算抓出架构师漏掉的需求，打回重做
     · S3/S4 门禁独立跑了 cmake，S5 门禁独立跑了 ctest，不采信产物里的自述
     · 门禁失败自动落学习事件，reflect 时蒸馏成经验并重算优先级
     · subagent 静默退出时靠产物判定真实进度，没有停下来等人

   在 CodeBuddy 里换成真实 Agent 跑：
     cd <你的仓库> && bash $REPO_ROOT/install.sh .
     然后在 CodeBuddy 中输入：/flow <需求描述>
EOF
printf "\n"
