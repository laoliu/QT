#!/usr/bin/env bash
# 分目录跑全量套件：单进程跑全量会在 ~46% 处段错误（OpenMP/原生库冲突），
# 分块跑既能拿到完整计数，也能把崩溃隔离在单个目录里。
# 用法: bash _suite_chunks.sh > _suite_chunks.log 2>&1
export KMP_DUPLICATE_LIB_OK=TRUE
export OMP_NUM_THREADS=1

run_chunk() {
    local label="$1"
    shift
    local out
    out=$(timeout 900 python -m pytest "$@" -q -p no:cacheprovider \
            --continue-on-collection-errors 2>&1)
    local rc=$?
    local summary
    summary=$(printf '%s\n' "$out" | grep -E "^[0-9]+ (passed|failed)|passed|failed|error" \
              | tail -1)
    printf 'CHUNK %-34s rc=%-3s %s\n' "$label" "$rc" "$summary"
    printf '%s\n' "$out" | grep -E "^(FAILED|ERROR)" | sed "s/^/    /"
}

for d in tests/*/; do
    [ -d "$d" ] || continue
    run_chunk "${d}" "$d"
done

# tests/ 下的顶层测试文件
run_chunk "tests/*.py(top-level)" $(ls tests/*.py)
echo "ALL CHUNKS DONE"
