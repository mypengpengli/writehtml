#!/usr/bin/env bash
# ===================================================================
# writehtml 一键部署 / 更新脚本（幂等，保留 ./data 数据）
# 用法：在 Ubuntu 服务器上直接粘贴整段执行即可（首次部署与后续更新同一脚本）
# -------------------------------------------------------------------
# 做的事：
#   1. 在 /opt/1panel/docker/compose/writehtml 建目录
#   2. git clone 最新代码到 /tmp 再覆盖过去（保留 ./data 里的数据库）
#   3. docker compose down + up -d --build --force-recreate
#   4. 打印容器状态
# 首次部署自动生成私有注册码写入 .env；更新不会覆盖。
# ===================================================================
set -e

DIR=/opt/1panel/docker/compose/writehtml
REPO=https://github.com/mypengpengli/writehtml.git
TMP=/tmp/writehtml-update

echo "[1/5] 准备目录 $DIR"
mkdir -p "$DIR"
cd "$DIR"

echo "[2/5] 拉取最新代码到 $TMP"
rm -rf "$TMP"
git clone --depth 1 "$REPO" "$TMP"

echo "[3/5] 覆盖代码（保留 ./data 数据库与本地数据）"
cp -rf "$TMP"/. "$DIR"/
rm -rf "$TMP"

NEW_SIGNUP_CODE=""
if [ ! -f .env ]; then
  NEW_SIGNUP_CODE="$(openssl rand -hex 16)"
  umask 077
  printf 'SIGNUP_CODE=%s\n' "$NEW_SIGNUP_CODE" > .env
fi

echo "[4/5] 重建容器"
docker compose down 2>/dev/null || true
docker rm -f writehtml 2>/dev/null || true
docker compose up -d --build --force-recreate

echo "[5/5] 容器状态"
docker compose ps

echo
echo "完成  访问 http://<服务器IP>:9123"
if [ -n "$NEW_SIGNUP_CODE" ]; then
  echo "首次注册注册码：$NEW_SIGNUP_CODE"
  echo "注册码已保存到 $DIR/.env（权限仅当前服务器用户可读）"
else
  echo "继续使用 $DIR/.env 中现有的注册配置"
fi
echo "数据保存在 $DIR/data/  （删除容器不会丢数据，删这个目录才会）"
