#!/usr/bin/env bash
# 数据已内置在 data/（共 700KB），一般不需要执行本脚本。
# 用途：数据被删、或想确认数据来源可复现时重新拉取。
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p data

fetch() {
  local name="$1" path="$2" min_size="$3"
  [ -s "data/$name" ] && {
    echo "跳过 $name（已存在 $(du -h "data/$name" | cut -f1)）"
    return 0
  }
  for base in https://hf-mirror.com https://huggingface.co; do
    echo "==> 下载 $name  from $base"
    if curl -fSL --retry 2 --connect-timeout 15 \
        -o "data/$name" "$base/$path"; then
      local size
      size=$(wc -c <"data/$name")
      if [ "$size" -ge "$min_size" ]; then
        echo "    OK  $(du -h "data/$name" | cut -f1)"
        return 0
      fi
      echo "    文件过小（$size 字节），换源重试"
      rm -f "data/$name"
    fi
  done
  echo "!! $name 下载失败，检查网络或代理"
  return 1
}

fetch b77_train.parquet \
  "datasets/PolyAI/banking77/resolve/main/data/train-00000-of-00001.parquet" 200000
fetch gsm8k_test.parquet \
  "datasets/openai/gsm8k/resolve/main/main/test-00000-of-00001.parquet" 300000

echo "完成。"
