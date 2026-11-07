# ResultBot terminal menu
livelogs() {
    printf '\nLIVE LOGS — Ctrl+C stops viewing; the bot keeps running.\n\n'
    journalctl --user -u resultbot-excellent-watch.service \
        -n 30 -f -o short-iso --no-pager
}

prevlogs() {
    journalctl --user -u resultbot-excellent-watch.service \
        -n 200 -o short-iso --no-pager
}

daylogs() {
    journalctl --user -u resultbot-excellent-watch.service \
        --since "24 hours ago" -o short-iso --no-pager
}

menu() {
    local choice
    while true; do
        printf '\n'
        printf '%s\n' \
            '========== RESULTBOT ==========' \
            ' 1. Bot status' \
            ' 2. Edit settings' \
            ' 3. Live logs' \
            ' 4. Recent logs (last 200 lines)' \
            ' 5. Last 24 hours logs' \
            ' 6. Dhan orders' \
            ' 7. Dhan funds' \
            ' 0. Exit menu' \
            '==============================='
        read -r -p 'Choose a number: ' choice || return
        case "$choice" in
            1) command bot status ;;
            2) command bot setup ;;
            3) livelogs ;;
            4) prevlogs ;;
            5) daylogs ;;
            6) command bot orders ;;
            7) command bot funds ;;
            0) return ;;
            *) printf 'Choose 0 to 7.\n'; continue ;;
        esac
        read -r -p 'Press Enter to return to the menu...' choice || return
    done
}

if [[ $- == *i* ]]; then
    printf '\nResultBot: type menu for numbered options.\n'
    printf 'Log shortcuts: livelogs | prevlogs | daylogs\n'
fi
