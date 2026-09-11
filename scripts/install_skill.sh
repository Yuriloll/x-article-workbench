#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
codex_dir=${CODEX_HOME:-"$HOME/.codex"}
skill_parent="$codex_dir/skills"
target="$skill_parent/x-article-workbench"

mkdir -p "$skill_parent"
if [ -L "$target" ]; then
  current=$(readlink "$target")
  if [ "$current" = "$project_dir" ]; then
    printf '%s\n' "Skill 已安装：$target"
    exit 0
  fi
  printf '%s\n' "目标位置已有其他符号链接：$target" >&2
  exit 2
fi
if [ -e "$target" ]; then
  printf '%s\n' "目标位置已存在，未覆盖：$target" >&2
  exit 2
fi
ln -s "$project_dir" "$target"
printf '%s\n' "Skill 已安装：$target -> $project_dir"
