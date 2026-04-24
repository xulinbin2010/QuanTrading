#!/bin/bash
# install_hooks.sh — 一键安装 git pre-push hook
#
# 用法：
#   bash scripts/install_hooks.sh
#
# 说明：
#   将 scripts/pre-push.hook 复制到 .git/hooks/pre-push 并设置可执行权限。
#   每次修改 scripts/pre-push.hook 后重新运行本脚本即可更新 hook。

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOK_SRC="${REPO_ROOT}/scripts/pre-push.hook"
HOOK_DST="${REPO_ROOT}/.git/hooks/pre-push"

# 检查源文件存在
if [ ! -f "${HOOK_SRC}" ]; then
    echo "ERROR: ${HOOK_SRC} not found."
    exit 1
fi

# 检查 .git 目录（确认在 git 仓库内）
if [ ! -d "${REPO_ROOT}/.git" ]; then
    echo "ERROR: ${REPO_ROOT}/.git not found. Run this script from within a git repository."
    exit 1
fi

# 备份已有 hook（若存在且不是我们自己的）
if [ -f "${HOOK_DST}" ] && ! grep -q "pre-push hook — 在 push 前自动检测" "${HOOK_DST}" 2>/dev/null; then
    BACKUP="${HOOK_DST}.backup.$(date +%Y%m%d%H%M%S)"
    cp "${HOOK_DST}" "${BACKUP}"
    echo "  Existing hook backed up to: ${BACKUP}"
fi

# 复制并设置权限
cp "${HOOK_SRC}" "${HOOK_DST}"
chmod +x "${HOOK_DST}"

echo ""
echo "  pre-push hook installed successfully."
echo "  Source : ${HOOK_SRC}"
echo "  Target : ${HOOK_DST}"
echo ""
echo "  The hook will run automatically on 'git push' when"
echo "  strategies/ or tests/ Python files are modified."
echo ""
echo "  Requirements:"
echo "    - .venv/bin/python must exist (python3.12 -m venv .venv)"
echo "    - scripts/detect_lookahead.py must exist"
echo "    - scripts/validate_change.py must exist (for baseline checks)"
echo ""
