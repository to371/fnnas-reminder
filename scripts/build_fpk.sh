#!/bin/bash
# 组装 fpk 工程并打包：./scripts/build_fpk.sh
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/fpk-src"
APP="$SRC/app"

# 图标先生成：make_icons.py 会写到 app/ui/images/（桌面入口必须在这里），
# 必须在同步之前跑，否则打包进去的还是上一版图标。
echo "==> 1/4 生成图标"
python "$ROOT/scripts/make_icons.py"

echo "==> 2/4 同步运行时代码 -> fpk-src/app/"
rm -rf "$APP"
mkdir -p "$APP"
# app/ 里同时包含运行代码、static/ 和 fnOS 桌面入口 ui/（ui 必须在 app/ 下，desktop_uidir 才会生效）
cp -r "$ROOT/app/." "$APP/"
# 清掉本地跑测试/调试留下的东西，绝对不能进安装包
find "$APP" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$APP" -maxdepth 1 -type d -name ".*" -exec rm -rf {} + 2>/dev/null || true
find "$APP" -maxdepth 1 -type d -name "data" -exec rm -rf {} + 2>/dev/null || true
find "$APP" \( -name "*.db" -o -name "*.db-wal" -o -name "*.db-shm" -o -name "*.pyc" \) -delete 2>/dev/null || true

echo "==> 3/4 修正脚本权限与换行"
# Windows 上 git 的 core.autocrlf 会把脚本转成 CRLF，装到 NAS 上会报
# "/bin/bash^M: bad interpreter"。.gitattributes 已经钉死 LF，这里再兜一层。
if command -v sed >/dev/null 2>&1; then
    for f in "$SRC"/cmd/* "$ROOT"/scripts/*.sh; do
        [ -f "$f" ] && sed -i 's/\r$//' "$f" 2>/dev/null || true
    done
fi
chmod +x "$SRC"/cmd/*
# 空 wizard 目录：官方 demo 包里也带着它，装上后是 /var/apps/{app}/wizard
mkdir -p "$SRC/wizard"

echo "==> 4/4 打包"
# 允许用 FNPACK 指定工具路径（例如 FNPACK=../tools/fnpack.exe）
FNPACK_BIN="${FNPACK:-}"
if [ -z "$FNPACK_BIN" ] || [ ! -x "$FNPACK_BIN" ]; then
    FNPACK_BIN="$(command -v fnpack || true)"
fi
if [ -z "$FNPACK_BIN" ]; then
    cat <<'EOF'
未找到 fnpack。请先下载官方打包工具：
  https://developer.fnnas.com/docs/cli/fnpack/
Windows 下载的文件没有后缀，需要重命名为 fnpack.exe 并加入 PATH，
或直接指定路径： FNPACK=/path/to/fnpack.exe ./scripts/build_fpk.sh
工程已组装完毕，安装 fnpack 后在本目录执行： fnpack build -d fpk-src
EOF
    exit 0
fi

cd "$ROOT"
"$FNPACK_BIN" build -d "$SRC"
echo "==> 完成，产物在 $ROOT/*.fpk"
