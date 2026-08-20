#!/bin/bash
# install_launchd.sh — 替代 mavis cron (minimax 平台绑定)
# 用 macOS launchd 设置定时任务, 跨机器友好 (用 git pull 后自动生效)
#
# 用法:
#   bash tools/install_launchd.sh                    # 安装默认任务
#   bash tools/install_launchd.sh uninstall          # 卸载
#   bash tools/install_launchd.sh status             # 查看状态

set -e

PLIST_DIR="$HOME/Library/LaunchAgents"
PROJECT_DIR="/Users/kurt/workspace/mavis-quant-agent"
PLIST_NAME="com.mavis-quant-agent.refresh"
PLIST_FILE="$PLIST_DIR/$PLIST_NAME.plist"

case "${1:-install}" in
    uninstall)
        launchctl unload "$PLIST_FILE" 2>/dev/null || true
        rm -f "$PLIST_FILE"
        echo "✅ 已卸载 $PLIST_NAME"
        ;;
    status)
        if launchctl list | grep -q "$PLIST_NAME"; then
            echo "✅ $PLIST_NAME 已加载"
            launchctl list | grep "$PLIST_NAME"
        else
            echo "❌ $PLIST_NAME 未加载"
        fi
        ;;
    install|*)
        mkdir -p "$PLIST_DIR"
        cat > "$PLIST_FILE" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_NAME</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$PROJECT_DIR/tools/refresh_data.sh</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$PROJECT_DIR</string>

    <key>StartInterval</key>
    <integer>3600</integer>  <!-- 1 小时 -->

    <key>StandardOutPath</key>
    <string>$PROJECT_DIR/data/refresh.log</string>

    <key>StandardErrorPath</key>
    <string>$PROJECT_DIR/data/refresh.err</string>

    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
EOF
        launchctl unload "$PLIST_FILE" 2>/dev/null || true
        launchctl load "$PLIST_FILE"
        echo "✅ 已安装 $PLIST_NAME, 每小时跑一次"
        echo "   日志: $PROJECT_DIR/data/refresh.log"
        echo "   卸载: bash tools/install_launchd.sh uninstall"
        ;;
esac
