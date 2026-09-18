#!/usr/bin/env bash
# ============================================================
# devflow 安装器 —— 把流水线部署到任意目标仓库
# ============================================================
#
#   ./install.sh <目标仓库路径> [--profile <名字>] [--force]
#
# 安装内容全部落在目标仓库的 .codebuddy/ 下：
#   agents/     7 个角色定义（CodeBuddy 会自动发现）
#   commands/   /flow 系列斜杠命令
#   hooks/      命令自动放行钩子
#   devflow/    引擎、配置、prompt 模板、经验库
#   settings.json
#
# 默认不覆盖目标仓库已有的同名文件（经验库和运行记录尤其不能覆盖），
# 加 --force 才会强制覆盖引擎与角色定义（经验库和 runs/ 永远不覆盖）。
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$SCRIPT_DIR/.codebuddy"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; DIM='\033[2m'; NC='\033[0m'
info()  { printf "  %b\n" "$1"; }
ok()    { printf "  ${GREEN}✓${NC} %b\n" "$1"; }
warn()  { printf "  ${YELLOW}!${NC} %b\n" "$1"; }
die()   { printf "${RED}✗ %b${NC}\n" "$1" >&2; exit 1; }

TARGET=""
PROFILE=""
FORCE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --profile) PROFILE="${2:-}"; shift 2 ;;
    --force)   FORCE=1; shift ;;
    -h|--help)
      sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    -*) die "未知参数：$1" ;;
    *)  TARGET="$1"; shift ;;
  esac
done

[ -n "$TARGET" ] || die "用法：./install.sh <目标仓库路径> [--profile <名字>] [--force]"
[ -d "$SRC" ]    || die "源目录不存在：$SRC"

mkdir -p "$TARGET"
TARGET="$(cd "$TARGET" && pwd)"

printf "\n${GREEN}devflow 安装器${NC}\n"
printf "${DIM}  源：  %s${NC}\n" "$SCRIPT_DIR"
printf "${DIM}  目标：%s${NC}\n\n" "$TARGET"

[ "$TARGET" = "$SCRIPT_DIR" ] && die "目标就是源仓库本身，无需安装"

DEST="$TARGET/.codebuddy"
mkdir -p "$DEST"

# ---------- 角色、命令、钩子、引擎 ----------
# 这些是 harness 本体，--force 时可整体覆盖
for dir in agents commands hooks; do
  if [ -d "$DEST/$dir" ] && [ "$FORCE" -eq 0 ]; then
    cp -rn "$SRC/$dir/." "$DEST/$dir/" 2>/dev/null || true
    warn "$dir/ 已存在，仅补充缺失文件（加 --force 可覆盖）"
  else
    mkdir -p "$DEST/$dir"
    cp -r "$SRC/$dir/." "$DEST/$dir/"
    ok "$dir/"
  fi
done

mkdir -p "$DEST/devflow"
for sub in bin engine config prompts dashboard; do
  [ -d "$SRC/devflow/$sub" ] || continue
  if [ -d "$DEST/devflow/$sub" ] && [ "$FORCE" -eq 0 ]; then
    cp -rn "$SRC/devflow/$sub/." "$DEST/devflow/$sub/" 2>/dev/null || true
    warn "devflow/$sub/ 已存在，仅补充缺失文件"
  else
    mkdir -p "$DEST/devflow/$sub"
    cp -r "$SRC/devflow/$sub/." "$DEST/devflow/$sub/"
    ok "devflow/$sub/"
  fi
done
chmod +x "$DEST/devflow/bin/devflow" 2>/dev/null || true
find "$DEST/devflow/engine" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

# ---------- 经验库：永不覆盖 ----------
# 经验库是目标仓库长期积累的资产，覆盖它等于把这个团队学到的东西全删了
mkdir -p "$DEST/devflow/learnings/events"
if [ -f "$DEST/devflow/learnings/active.yaml" ]; then
  warn "经验库已存在，保留不动（这是该仓库积累的资产）"
else
  cp "$SRC/devflow/learnings/active.yaml" "$DEST/devflow/learnings/active.yaml"
  ok "devflow/learnings/ （含 4 条预置 P0 铁律）"
fi
mkdir -p "$DEST/devflow/runs"

# ---------- settings.json ----------
if [ -f "$DEST/settings.json" ] && [ "$FORCE" -eq 0 ]; then
  warn "settings.json 已存在，保留不动"
  warn "  需要命令自动放行的话，手工把 PreToolUse hook 合并进去"
  info "  参考：$SRC/settings.json"
else
  cp "$SRC/settings.json" "$DEST/settings.json"
  ok "settings.json"
fi

# ---------- .gitignore ----------
GITIGNORE="$TARGET/.gitignore"
add_ignore() {
  grep -qxF "$1" "$GITIGNORE" 2>/dev/null || echo "$1" >> "$GITIGNORE"
}
if [ -d "$TARGET/.git" ] || [ -f "$GITIGNORE" ]; then
  touch "$GITIGNORE"
  grep -q "devflow 运行产物" "$GITIGNORE" 2>/dev/null || {
    printf '\n# devflow 运行产物（每次运行的状态与产物，不入库）\n' >> "$GITIGNORE"
  }
  add_ignore ".codebuddy/devflow/runs/"
  add_ignore ".codebuddy/devflow/repo-map.md"
  add_ignore ".codebuddy/devflow/engine/__pycache__/"
  ok ".gitignore 已追加忽略规则"
  info "${DIM}  注意：learnings/ 没有被忽略 —— 经验库应该入库，这样团队共享积累${NC}"
fi

# ---------- 自检 ----------
printf "\n"
DF="$DEST/devflow/bin/devflow"
if [ -n "$PROFILE" ]; then
  if [ -f "$DEST/devflow/config/profiles/$PROFILE.yaml" ]; then
    python3 - "$DEST/devflow/config/workflow.yaml" "$PROFILE" <<'PY'
import re, sys
path, profile = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as f:
    text = f.read()
text = re.sub(r"(\n  profile:\s*)\S+", r"\g<1>" + profile, text, count=1)
with open(path, "w", encoding="utf-8") as f:
    f.write(text)
PY
    ok "默认 profile 已设为 $PROFILE"
  else
    warn "profile '$PROFILE' 不存在，保持默认"
  fi
fi

printf "${GREEN}安装完成${NC}\n\n"
printf "接下来：\n"
printf "  ${DIM}# 1. 环境自检（确认 profile 里的构建/测试命令在本机可用）${NC}\n"
printf "  cd %s && .codebuddy/devflow/bin/devflow doctor\n\n" "$TARGET"
printf "  ${DIM}# 2. 生成仓库地图${NC}\n"
printf "  .codebuddy/devflow/bin/devflow repo-map\n\n"
printf "  ${DIM}# 3. 在 CodeBuddy 里跑完整流水线${NC}\n"
printf "  /flow <你的需求描述>\n\n"
printf "${DIM}技术栈不是 C/C++ 的话，先复制一份 profile 改构建命令：${NC}\n"
printf "${DIM}  cd .codebuddy/devflow/config/profiles${NC}\n"
printf "${DIM}  cp default.yaml myteam.yaml   # 改 commands.build 和 commands.test_unit${NC}\n"
printf "${DIM}  devflow doctor --profile myteam${NC}\n\n"
