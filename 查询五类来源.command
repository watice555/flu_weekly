#!/bin/zsh
# Resolve the project directory even when launched by double-clicking in Finder.
cd -- "${0:A:h}" || exit 1

if [[ ! -x .venv/bin/python ]]; then
    print -u2 -- "未找到项目 Python 环境，请按 README 创建 .venv 后重试。"
    if (( $# == 0 )) && [[ -t 0 ]]; then
        read -r "?按回车关闭……"
    fi
    exit 1
fi

.venv/bin/python -m flu_data.source_table --open "$@"
query_exit_code=$?
if (( query_exit_code != 0 && $# == 0 )) && [[ -t 0 ]]; then
    read -r "?按回车关闭……"
fi
exit "$query_exit_code"
