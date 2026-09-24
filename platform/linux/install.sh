#!/bin/sh
# WorkTime Logger — Linux (Debian, i3, rofi, polybar) launcher layer installer.
#
# Installs:
#   - 5 .desktop launcher files in $XDG_DATA_HOME/applications (default
#     ~/.local/share/applications) — Start, Stop, Dashboard, Weekly Report,
#     Monthly Report — findable through rofi's app launcher (or any other
#     .desktop-aware launcher / menu).
#   - a 'worktime' symlink in ~/.local/bin, for terminal use.
#   - prints the i3 keybinding lines to paste into your own i3 config.
#   - prints a polybar module snippet, and hints for adding it.
#
# It NEVER edits your i3 config, polybar config (or any other window
# manager / status bar config) automatically — you paste the printed
# lines in yourself.
#
# Usage:
#   install.sh              install (safe to run more than once)
#   install.sh --uninstall  remove what this script installed
#   install.sh -h|--help    show this help
set -eu

usage() {
    cat <<'EOF'
Usage: install.sh [--uninstall|-h|--help]

Install WorkTime Logger's Linux launcher layer:
  - 5 rofi/app-launcher-searchable .desktop entries in
    $XDG_DATA_HOME/applications (default ~/.local/share/applications)
  - a 'worktime' symlink in ~/.local/bin
  - prints i3 keybinding lines for you to paste in manually
  - prints a polybar module snippet for you to paste in manually

Options:
  --uninstall   remove the .desktop entries and symlink installed by this script
  -h, --help    show this help and exit
EOF
}

case "${1:-}" in
    "")
        mode=install
        ;;
    --uninstall)
        mode=uninstall
        ;;
    -h|--help)
        usage
        exit 0
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac

allow_any_os() {
    # Test-only hook so the tests can run on macOS.
    case "${WORKTIME_INSTALL_ALLOW_ANY_OS:-}" in
        1|true|yes) return 0 ;;
        *) return 1 ;;
    esac
}

if [ "$(uname -s)" != "Linux" ] && ! allow_any_os; then
    echo "worktime install: Linux only — on macOS use platform/macos/install.sh" >&2
    exit 1
fi

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd -P)
REPO=$(cd "$SCRIPT_DIR/../.." && pwd -P)
WT="$REPO/bin/worktime"
TEMPLATE="$SCRIPT_DIR/i3-bindings.conf"
POLYBAR_TEMPLATE="$SCRIPT_DIR/polybar-module.ini"
ICON="$REPO/platform/linux/icons/worktime.svg"

case "$REPO" in
    *[!A-Za-z0-9._/-]*)
        echo "worktime install: repo path must not contain spaces or special characters (needed for i3 exec and .desktop Exec lines): $REPO" >&2
        exit 1
        ;;
esac

should_register() {
    case "${WORKTIME_INSTALL_NO_REGISTER:-}" in
        1|true|yes) return 1 ;;
        *) return 0 ;;
    esac
}

# make_desktop ID NAME DESC ARGS...
make_desktop() {
    id="$1"
    name="$2"
    desc="$3"
    shift 3
    args="$*"
    entry="$APPS_DIR/$id.desktop"

    if [ -e "$entry" ] && ! grep -F -q "X-WorkTime-Launcher=true" "$entry" 2>/dev/null; then
        echo "Skipping $id.desktop: exists and was not created by WorkTime" >&2
        return 0
    fi

    tmp="$entry.tmp"
    cat > "$tmp" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=$name
Comment=WorkTime Logger: $desc
Exec=$WT $args
Terminal=false
Icon=$ICON
Categories=Utility;
Keywords=worktime;time;tracking;
X-WorkTime-Launcher=true
EOF
    chmod 644 "$tmp"
    mv "$tmp" "$entry"
    echo "$entry"
}

# remove_desktop ID
remove_desktop() {
    id="$1"
    entry="$APPS_DIR/$id.desktop"
    if [ -f "$entry" ]; then
        if grep -F -q "X-WorkTime-Launcher=true" "$entry" 2>/dev/null; then
            rm -f "$entry"
            echo "Removed $entry"
        else
            echo "Skipping $id.desktop: exists and was not created by WorkTime" >&2
        fi
    fi
}

do_install() {
    if ! "$WT" --version >/dev/null 2>&1; then
        echo "worktime install: bin/worktime could not find Python >= 3.11 (Debian 12+ ships 3.11; or set WORKTIME_PYTHON)" >&2
        exit 1
    fi

    missing_tools=""
    missing_pkgs=""
    if ! command -v notify-send >/dev/null 2>&1; then
        missing_tools="notify-send"
        missing_pkgs="libnotify-bin"
    fi
    if ! command -v xdg-open >/dev/null 2>&1; then
        if [ -n "$missing_tools" ]; then
            missing_tools="$missing_tools xdg-open"
            missing_pkgs="$missing_pkgs xdg-utils"
        else
            missing_tools="xdg-open"
            missing_pkgs="xdg-utils"
        fi
    fi
    if [ -n "$missing_tools" ]; then
        echo "Missing: $missing_tools" >&2
        echo "  sudo apt install $missing_pkgs" >&2
        case " $missing_tools " in
            *" notify-send "*)
                echo "Notifications also need a notification daemon, e.g. dunst (sudo apt install dunst)." >&2
                ;;
        esac
    fi

    APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
    mkdir -p "$APPS_DIR"

    echo "Creating rofi/app-launcher entries in $APPS_DIR:"
    make_desktop worktime-start "WorkTime Start" "start a session or show the running time" start
    make_desktop worktime-stop "WorkTime Stop" "stop the running session" stop
    make_desktop worktime-dashboard "WorkTime Dashboard" "open the dashboard" dashboard
    make_desktop worktime-report-week "WorkTime Weekly Report" "weekly report (this week so far)" report week
    make_desktop worktime-report-month "WorkTime Monthly Report" "monthly report (this month so far)" report month

    if should_register && command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
    fi

    mkdir -p "$HOME/.local/bin"
    L="$HOME/.local/bin/worktime"
    if [ -e "$L" ] && [ ! -L "$L" ]; then
        echo "Skipping ~/.local/bin/worktime: a file already exists there" >&2
    else
        ln -sfn "$WT" "$L"
        echo "Symlinked $L -> $WT"
    fi

    case ":$PATH:" in
        *":$HOME/.local/bin:"*)
            ;;
        *)
            echo ""
            echo "Add this to ~/.profile (or ~/.bashrc) to use 'worktime' in the terminal:"
            printf '  export PATH="%s/.local/bin:$PATH"\n' "$HOME"
            echo "(On Debian, ~/.profile already adds ~/.local/bin to PATH the next time you log in, once the directory exists.)"
            ;;
    esac

    echo ""
    echo 'i3: paste these lines into your i3 config, then reload i3 ($mod+shift+c):'
    sed -e '/^#/d' -e '/^[[:space:]]*$/d' "$TEMPLATE" | sed "s|@WORKTIME@|$WT|g"

    CONF=""
    for cfg in "$HOME/.config/i3/config" "$HOME/.i3/config"; do
        if [ -f "$cfg" ]; then
            CONF="$cfg"
            break
        fi
    done

    if [ -n "$CONF" ]; then
        for key in t u d w; do
            pattern='^[[:space:]]*bindsym[[:space:]]+(--[a-z-]+[[:space:]]+)*((\$mod|mod1|mod4)\+shift|shift\+(\$mod|mod1|mod4))\+'
            pattern="$pattern$key"'([[:space:]]|$)'
            line=$(grep -Ei "$pattern" "$CONF" 2>/dev/null | head -n 1 || true)
            if [ -n "$line" ]; then
                bind='$mod+shift+'"$key"
                case "$line" in
                    *[Ww][Oo][Rr][Kk][Tt][Ii][Mm][Ee]*)
                        echo "$bind: already configured"
                        ;;
                    *)
                        echo "Warning: $bind is already bound in $CONF: $line"
                        ;;
                esac
            fi
        done
    fi

    # rofi: find the app-launcher key combo (if any) so the hint below is accurate.
    rofi_key=""
    if [ -n "$CONF" ]; then
        rofi_line=$(grep -Ei '^[[:space:]]*bindsym[[:space:]]' "$CONF" 2>/dev/null \
            | grep -i 'rofi' \
            | grep -iE 'drun|app-launcher' \
            | head -n 1 || true)
        if [ -n "$rofi_line" ]; then
            rofi_key=$(printf '%s\n' "$rofi_line" | awk '
                {
                    for (i = 1; i <= NF; i++) {
                        if (tolower($i) == "bindsym") {
                            j = i + 1
                            while (j <= NF && $j ~ /^--/) j++
                            if (j <= NF) {
                                print $j
                            }
                            exit
                        }
                    }
                }')
        fi
    fi

    echo ""
    echo "polybar: add this module to ~/.config/polybar/config.ini:"
    sed -e '/^;/d' -e '/^[[:space:]]*$/d' "$POLYBAR_TEMPLATE" | sed "s|@WORKTIME@|$WT|g"

    POLY_CONF=""
    for cfg in "$HOME/.config/polybar/config.ini" "$HOME/.config/polybar/config"; do
        if [ -f "$cfg" ]; then
            POLY_CONF="$cfg"
            break
        fi
    done

    if [ -n "$POLY_CONF" ]; then
        if grep -F -q '[module/worktime]' "$POLY_CONF" 2>/dev/null; then
            echo "polybar: worktime module already configured"
        else
            mr_line=$(grep -E '^[[:space:]]*modules-right[[:space:]]*=' "$POLY_CONF" 2>/dev/null | head -n 1 || true)
            if [ -n "$mr_line" ]; then
                already=$(printf '%s\n' "$mr_line" | awk -F'=' '
                    {
                        val = $2
                        n = split(val, arr, /[ \t]+/)
                        for (i = 1; i <= n; i++) {
                            if (arr[i] == "worktime") {
                                print "yes"
                                exit
                            }
                        }
                    }')
                if [ "$already" = "yes" ]; then
                    echo "polybar: 'worktime' is already in modules-right"
                else
                    new_line=$(printf '%s\n' "$mr_line" | awk -F'=' '
                        {
                            key = $1
                            val = $2
                            gsub(/^[ \t]+|[ \t]+$/, "", key)
                            gsub(/^[ \t]+|[ \t]+$/, "", val)
                            n = split(val, arr, /[ \t]+/)
                            inserted = 0
                            out = ""
                            for (i = 1; i <= n; i++) {
                                if (arr[i] == "time" && inserted == 0) {
                                    out = out "worktime "
                                    inserted = 1
                                }
                                out = out arr[i] " "
                            }
                            if (inserted == 0) {
                                out = out "worktime "
                            }
                            gsub(/[ \t]+$/, "", out)
                            print key " = " out
                        }')
                    echo "polybar: and add 'worktime' to modules-right, e.g.:"
                    echo "$new_line"
                fi
            else
                echo "polybar: add 'worktime' to one of your modules-left/center/right lines."
            fi
        fi
    else
        echo "(If you use polybar: add 'worktime' to one of your modules-left/center/right lines in ~/.config/polybar/config.ini.)"
    fi

    echo ""
    echo "Done. Installed:"
    echo "  $APPS_DIR/worktime-start.desktop"
    echo "  $APPS_DIR/worktime-stop.desktop"
    echo "  $APPS_DIR/worktime-dashboard.desktop"
    echo "  $APPS_DIR/worktime-report-week.desktop"
    echo "  $APPS_DIR/worktime-report-month.desktop"
    echo "  $L"
    echo "  $ICON"
    echo ""
    if [ -n "$rofi_key" ]; then
        echo "rofi: open your app launcher ($rofi_key) and type 'worktime'."
    else
        echo "rofi: open your app launcher (rofi -show drun) and type 'worktime'."
    fi
}

do_uninstall() {
    APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"

    remove_desktop worktime-start
    remove_desktop worktime-stop
    remove_desktop worktime-dashboard
    remove_desktop worktime-report-week
    remove_desktop worktime-report-month

    if should_register && command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
    fi

    L="$HOME/.local/bin/worktime"
    if [ -L "$L" ]; then
        target=$(readlink "$L")
        if [ "$target" = "$WT" ]; then
            rm -f "$L"
            echo "Removed $L"
        fi
    fi

    echo 'Remove the WorkTime bindsym lines ($mod+shift+t/u/d/w) from your i3 config manually.'
    echo "Remove the [module/worktime] block, and the 'worktime' token from modules-right (or wherever you added it), from your polybar config manually."
}

if [ "$mode" = "install" ]; then
    do_install
else
    do_uninstall
fi
