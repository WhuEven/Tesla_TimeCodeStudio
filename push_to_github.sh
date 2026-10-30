#!/bin/bash
# 把本地仓库推送到 GitHub。
# 用法：先复制仓库地址（SSH 形式），然后：
#     ./push_to_github.sh git@github.com:<你的用户名>/<仓库名>.git
#
# 前提：已把 ~/.ssh/id_ed25519_timecodestudio.pub 添加到 GitHub →
#       Settings → SSH and GPG keys → New SSH key

set -e
cd "$(dirname "$0")"

REMOTE="${1:-}"
if [ -z "$REMOTE" ]; then
    echo "用法: $0 git@github.com:<用户名>/<仓库名>.git"
    exit 1
fi

export GIT_SSH_COMMAND="ssh -i $HOME/.ssh/id_ed25519_timecodestudio -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

git remote remove origin 2>/dev/null || true
git remote add origin "$REMOTE"
git branch -M main

echo "推送到 $REMOTE ..."
git push -u origin main

echo
echo "完成。仓库地址: ${REMOTE%.git}"
