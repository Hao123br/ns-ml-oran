#!/bin/sh
modprobe zram
zramctl /dev/zram0 --algorithm zstd --size 32G
mke2fs -q -m 0 -b 4096 -O sparse_super -L zram /dev/zram0
mount -o relatime,noexec,nosuid /dev/zram0 ./zram
chown hao123:hao123 zram
