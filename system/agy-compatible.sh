#!/bin/sh
# Chạy AGY native khi CPU đủ tập lệnh; fallback sang ARM64 + QEMU trên VPS x86 cũ.
set -eu

native="${JAVIS_ANTIGRAVITY_NATIVE_BIN:-/usr/local/lib/javis-antigravity/native/agy}"
arm64="${JAVIS_ANTIGRAVITY_ARM64_BIN:-/usr/local/lib/javis-antigravity/arm64/agy}"
qemu="${JAVIS_ANTIGRAVITY_QEMU_BIN:-/usr/bin/qemu-aarch64}"
ld_prefix="${JAVIS_ANTIGRAVITY_QEMU_LD_PREFIX:-/usr/aarch64-linux-gnu}"
cpuinfo="${JAVIS_ANTIGRAVITY_CPUINFO:-/proc/cpuinfo}"
force="${JAVIS_ANTIGRAVITY_FORCE_EMULATION:-0}"

need_emulation=0
if [ "$force" = "1" ] || [ "$force" = "true" ]; then
    need_emulation=1
elif [ "$(uname -m)" = "x86_64" ] && [ -r "$cpuinfo" ]; then
    # Tên feature của binary Google -> tên flag Linux /proc/cpuinfo:
    # cmpxchg16b=cx16, sse3=pni, pclmul=pclmulqdq.
    flags="$(sed -n 's/^flags[[:space:]]*:[[:space:]]*//p' "$cpuinfo" | head -n 1)"
    for flag in mmx pclmulqdq popcnt sse sse2 pni ssse3 sse4_1 sse4_2 cx16; do
        case " $flags " in
            *" $flag "*) ;;
            *) need_emulation=1; break ;;
        esac
    done
fi

if [ "$need_emulation" = "1" ]; then
    if [ ! -x "$qemu" ] || [ ! -x "$arm64" ] || [ ! -d "$ld_prefix" ]; then
        echo "Antigravity cần CPU pclmul/sse4 hoặc fallback ARM64-QEMU, nhưng fallback chưa được cài đủ." >&2
        exit 126
    fi
    exec "$qemu" -L "$ld_prefix" "$arm64" "$@"
fi

if [ ! -x "$native" ]; then
    echo "Không tìm thấy Antigravity CLI native tại $native." >&2
    exit 127
fi
exec "$native" "$@"
