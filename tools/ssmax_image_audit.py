#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ssmax_image_audit.py - compare a self-built SeedSigner OS image with the official one.

    python3 ssmax_image_audit.py OFFICIAL.img CANDIDATE.img [--app-tree DIR] [--app-rev REV]

Goal: prove, from the two image files alone, that the operating-system part of
CANDIDATE is bit-for-bit the official SeedSigner OS, and that the only differences are
in the application directory /opt/src (and, with --app-tree, that those differences
are exactly the files of the reviewed application source tree).

Everything is read-only: the images are opened for reading only, nothing is mounted,
no root rights are needed and only the Python 3 standard library is used.  (With
--app-tree, `git` is used read-only if it is installed; it is optional.)

How a SeedSigner pi0 image is built (seedsigner-os, opt/pi0/board/post-image-seedsigner.sh):
  * a 50 MB disk image with an MBR and one FAT partition, created with
    mkfs.vfat --invariant and filled with mcopy;
  * the FAT partition holds the Raspberry Pi firmware, the device trees, config.txt,
    cmdline.txt and zImage;
  * zImage is the Linux kernel, and the WHOLE root filesystem is built into it as an
    "initramfs" (kernel.config: CONFIG_INITRAMFS_SOURCE=rootfs.cpio, gzip-compressed;
    the kernel itself is gzip-compressed inside zImage).
So the steps are:
  [1] disk layout (MBR)       [2] boot partition files       [3] kernel
  [4] root filesystem         [5] /opt/src (application)     [6] summary

What "kernel code identical modulo relocation" means (step [3]):
  The kernel binary necessarily differs, because the root filesystem is inside it and
  everything placed after it moves.  The tool decompresses both kernels and requires
  that the code and data before the initramfs have the same size, that the part after
  it has the same size, and that every 32-bit word that differs is (a) a pointer into
  the moved part, changed by exactly the distance it moved (absolute address; offset in
  the kallsyms table; or one of a few listed PC-relative literals), (b) the GNU build-id,
  which is a hash over the whole kernel, or (c) the build timestamp string, or (d) in the
  kallsyms name table, symbols that share one address listed in another order (when the
  initramfs ends on an 8-byte boundary, __irf_end and __initramfs_size get the same
  address and their names can swap; accepted only if the names are the same, the
  token tables identical and each reordered name keeps the address it had, relocated).
  Any changed instruction or data value outside those rules makes the check fail.
  Validated on a real pair: the official 0.8.5 and 0.8.6 pi0 kernels (same kernel build,
  different initramfs) - all ~24,000 differing words are explained.

Limitations (see also the README, section "Verify the image"):
  * The kernel check cannot see a pointer that SHOULD have been relocated but was not,
    and would accept a pointer-shaped constant changed by exactly the shift; both need a
    deliberately manipulated toolchain.  The strongest kernel check remains rebuilding
    the official image in the same environment and comparing its published sha256.
  * Bytecode bodies of changed .py files are verified only when the tool runs on
    CPython 3.12 (3.12.10 = byte-for-byte; other 3.12.x = same bytecode).
  * Fonts/.mo generated with other tool versions can differ; .mo files are compared
    message by message, fonts are only reported (not verified).  The NotoSans JP/KR/SC
    subsets are used to draw text, digits included, when the device is set to Japanese,
    Korean or Chinese.

Exit status:
  0  OS part identical to the official image: every boot file except zImage is
     identical, the kernel code is identical (apart from the embedded initramfs and the
     address shift it causes) and every root-filesystem entry outside /opt/src is
     identical.  Only /opt/src differs, every check on /opt/src passed, and every file
     of /opt/src matches the app tree given with --app-tree (at --app-rev).
     Without --app-tree the tool never exits with 0: see 4.
  1  something OUTSIDE /opt/src differs, or could not be proven identical.
  2  usage error, or an input could not be read or parsed.
  3  OS part identical, but a check on /opt/src failed (bytecode that does not match
     its source, or, with --app-tree, a file that does not match the app tree).
  4  OS part identical and nothing failed, but not everything could be verified: no
     --app-tree was given (so /opt/src was not compared with any source tree), or the
     bytecode of changed files could not be recompiled by the Python running the tool
     (it needs the same CPython version as the image, 3.12 for SeedSigner 0.8.7).
"""

import argparse
import hashlib
import importlib.util
import marshal
import os
import re
import struct
import subprocess
import sys
import zlib

TOOL_VERSION = "1.0"

APP_DIR = "/opt/src"                    # the only directory allowed to differ
MAX_INFLATE = 512 * 1024 * 1024         # never decompress more than this (zip-bomb guard)
MASK32 = 0xFFFFFFFF

# Paths inside /opt/src that are produced by the build itself (seedsigner-os opt/build.sh)
TRANSLATIONS_DIR = APP_DIR + "/seedsigner/resources/seedsigner-translations"
EGG_INFO_DIR = APP_DIR + "/seedsigner.egg-info"


class AuditError(Exception):
    """An input could not be read or parsed.  The tool then exits with status 2."""


# --------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------

def u16(buf, off):
    return struct.unpack_from("<H", buf, off)[0]


def u32(buf, off):
    return struct.unpack_from("<I", buf, off)[0]


def align_up(value, alignment):
    return (value + alignment - 1) // alignment * alignment


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def git_blob_id(data):
    """The id git gives to a file with this content (sha1 of 'blob <size>\\0' + data)."""
    h = hashlib.sha1()
    h.update(b"blob %d\x00" % len(data))
    h.update(data)
    return h.hexdigest()


def is_zero(buf, start, end):
    """True if buf[start:end] contains only zero bytes (no copy is made)."""
    if end <= start:
        return True
    return buf.count(b"\x00", start, end) == end - start


def inflate_gzip(buf, offset, limit=MAX_INFLATE):
    """Decompress one gzip member starting at buf[offset].

    Returns (data, compressed_length).  zlib checks the gzip CRC-32 and length trailer,
    so a member that decompresses without error is intact.  Raises zlib.error or
    AuditError if the member is corrupt, truncated or too large."""
    d = zlib.decompressobj(16 + 15)                  # 16+15 = expect a gzip header
    chunks, total, pos = [], 0, offset
    while not d.eof:
        if pos >= len(buf):
            raise AuditError("gzip data at offset %d is truncated" % offset)
        piece = buf[pos:pos + 65536]                 # small pieces: at most ~64 MB out per step
        pos += len(piece)
        out = d.decompress(piece)
        total += len(out)
        if total > limit:
            raise AuditError("gzip data at offset %d inflates to more than %d bytes" % (offset, limit))
        chunks.append(out)
    used = pos - offset - len(d.unused_data)         # bytes of buf that belong to this member
    return b"".join(chunks), used


def plural(n, word):
    if n == 1:
        return "1 " + word
    return "%d %s" % (n, word[:-1] + "ies" if word.endswith("y") else word + "s")


# --------------------------------------------------------------------------------------
# Report: prints the detailed output and remembers the findings for the verdict
# --------------------------------------------------------------------------------------

class Report:
    GROUP_TITLES = {
        "disk": "disk layout",
        "boot": "boot partition",
        "kernel": "kernel",
        "rootfs": "root filesystem outside /opt/src",
    }

    def __init__(self, verbose, max_list):
        self.verbose = verbose
        self.max_list = max_list
        self.os_problems = []      # (group, text): differences OUTSIDE /opt/src -> exit 1
        self.app_problems = []     # failed checks on /opt/src -> exit 3
        self.warnings = []         # noteworthy facts that do not change the result
        self.incomplete = []       # checks that could not be completed -> exit 4
        self.summary = []          # (label, result) lines for the final table

    @staticmethod
    def section(title):
        print()
        print("=" * 79)
        print(title)
        print("=" * 79)

    @staticmethod
    def sub(title):
        print()
        print("--- " + title)

    @staticmethod
    def line(text=""):
        print(text)

    def os_problem(self, group, text, show=True):
        self.os_problems.append((group, text))
        if show:
            print("  [!!] OUTSIDE /opt/src: " + text)

    def app_problem(self, text, show=True):
        self.app_problems.append(text)
        if show:
            print("  [FAIL] " + text)

    def warn(self, text, show=True):
        self.warnings.append(text)
        if show:
            print("  [warning] " + text)

    def capped(self, items, printer, what):
        """Print at most max_list items (all of them with --verbose)."""
        limit = None if self.verbose else self.max_list
        for i, item in enumerate(items):
            if limit is not None and i >= limit:
                print("  ... %d more %s not shown (use --verbose to list all)" % (len(items) - limit, what))
                break
            printer(item)


# --------------------------------------------------------------------------------------
# [1] MBR partition table
# --------------------------------------------------------------------------------------

FAT_PARTITION_TYPES = {0x01: "FAT12", 0x04: "FAT16 <32M", 0x06: "FAT16", 0x0B: "W95 FAT32",
                       0x0C: "W95 FAT32 LBA", 0x0E: "W95 FAT16 LBA"}


class Partition:
    def __init__(self, index, boot_flag, ptype, first_lba, sectors):
        self.index, self.boot_flag, self.ptype = index, boot_flag, ptype
        self.first_lba, self.sectors = first_lba, sectors

    def describe(self):
        return "partition %d: type 0x%02x (%s)%s, start sector %d, %d sectors (%d bytes)" % (
            self.index, self.ptype, FAT_PARTITION_TYPES.get(self.ptype, "other"),
            ", bootable" if self.boot_flag == 0x80 else "", self.first_lba, self.sectors,
            self.sectors * 512)


def parse_mbr(img, label):
    """Read the four primary partition entries of the MBR (sector 0)."""
    if len(img) < 1024 or img[510:512] != b"\x55\xaa":
        raise AuditError("%s: no MBR partition table (missing 55AA signature)" % label)
    parts = []
    for i in range(4):
        e = img[446 + 16 * i:446 + 16 * (i + 1)]
        ptype, first, count = e[4], u32(e, 8), u32(e, 12)
        if ptype == 0 and count == 0:
            continue
        parts.append(Partition(i + 1, e[0], ptype, first, count))
    return parts


def fat_partition(parts, img, label):
    fats = [p for p in parts if p.ptype in FAT_PARTITION_TYPES]
    if len(fats) != 1:
        raise AuditError("%s: expected exactly one FAT partition, found %d" % (label, len(fats)))
    p = fats[0]
    if (p.first_lba + p.sectors) * 512 > len(img):
        raise AuditError("%s: the FAT partition extends beyond the end of the image" % label)
    return p


def audit_disk_layout(off_img, cand_img, off_parts, cand_parts, rep):
    rep.section("[1] Disk layout (MBR partition table)")
    for label, img, parts in (("official ", off_img, off_parts), ("candidate", cand_img, cand_parts)):
        rep.line("  %s: %d bytes, disk signature 0x%08x" % (label, len(img), u32(img, 440)))
        for p in parts:
            rep.line("             " + p.describe())
    ok = True
    if off_img[:512] != cand_img[:512]:
        ok = False
        rep.os_problem("disk", "the MBR (sector 0) differs between the two images")
    if len(off_img) != len(cand_img):
        ok = False
        rep.os_problem("disk", "the image sizes differ (%d vs %d bytes)" % (len(off_img), len(cand_img)))
    # Everything that is not inside a partition must be zero: the boot-code area of the
    # MBR (the Raspberry Pi does not use it), the gap before the first partition and any
    # space after the last one.  Data hidden there would never be read by the device,
    # but a clean image has none, so we insist on it.
    for label, img, parts in (("official", off_img, off_parts), ("candidate", cand_img, cand_parts)):
        used = sorted((p.first_lba * 512, (p.first_lba + p.sectors) * 512) for p in parts)
        gaps = [(0, 440)]                      # MBR boot code (disk signature at 440)
        pos = 512
        for start, end in used:
            gaps.append((pos, start))
            pos = max(pos, end)
        gaps.append((pos, len(img)))
        for start, end in gaps:
            if not is_zero(img, start, end):
                msg = "%s: non-zero bytes outside the partitions (bytes %d-%d)" % (label, start, end)
                if label == "candidate":
                    ok = False
                    rep.os_problem("disk", msg)
                else:
                    rep.warn(msg)
    if ok:
        rep.line("  MBR identical, image sizes identical, no data outside the partition.")
    rep.summary.append(("Disk layout (MBR)", "identical" if ok else "DIFFERENT"))


# --------------------------------------------------------------------------------------
# [2] FAT12/16/32 reader (enough for images made by mkfs.vfat + mcopy)
# --------------------------------------------------------------------------------------

def fat_datetime(date, time=0, tenths=0):
    """Decode a FAT date/time pair into a readable string."""
    if date == 0 and time == 0:
        return "-"
    y, mo, d = 1980 + (date >> 9), (date >> 5) & 15, date & 31
    h, mi, s = time >> 11, (time >> 5) & 63, (time & 31) * 2 + tenths // 100
    return "%04d-%02d-%02d %02d:%02d:%02d" % (y, mo, d, h, mi, s)


def lfn_checksum(short_name):
    """Checksum of an 8.3 name, stored in each long-file-name entry that belongs to it."""
    s = 0
    for c in short_name:
        s = (((s & 1) << 7) + (s >> 1) + c) & 0xFF
    return s


def short_name_text(raw, nt_flags):
    """Turn the 11-byte 8.3 name into text, honouring the lower-case flags that
    Windows NT / Linux / mtools store in byte 12 of the directory entry."""
    base, ext = raw[:8].rstrip(b" "), raw[8:11].rstrip(b" ")
    if base[:1] == b"\x05":
        base = b"\xe5" + base[1:]
    base, ext = base.decode("cp437"), ext.decode("cp437")
    if nt_flags & 0x08:
        base = base.lower()
    if nt_flags & 0x10:
        ext = ext.lower()
    return base + ("." + ext if ext else "")


class FatEntry:
    def __init__(self, path, is_dir, attr, size, short, nt_flags, times, data):
        self.path, self.is_dir, self.attr, self.size = path, is_dir, attr, size
        self.short, self.nt_flags, self.times, self.data = short, nt_flags, times, data
        self.sha256 = sha256(data) if not is_dir else None

    def meta(self):
        """Metadata compared between the images (timestamps are reported separately)."""
        return {"type": "dir" if self.is_dir else "file", "attributes": "0x%02x" % self.attr,
                "size": self.size, "8.3 name": self.short}


class FatFilesystem:
    """Minimal read-only FAT12/16/32 reader with long-file-name (VFAT) support."""

    def __init__(self, img, part, label):
        self.img, self.label, self.anomalies = img, label, []
        base = part.first_lba * 512
        bs = img[base:base + 512]
        self.bps, self.spc, self.reserved, self.nfats = u16(bs, 11), bs[13], u16(bs, 14), bs[16]
        self.root_entries = u16(bs, 17)
        if (self.bps not in (512, 1024, 2048, 4096) or self.spc == 0 or self.spc & (self.spc - 1)
                or self.reserved == 0 or self.nfats == 0 or bs[510:512] != b"\x55\xaa"):
            raise AuditError("%s: the first partition does not start with a FAT boot sector" % label)
        self.total_sectors = u16(bs, 19) or u32(bs, 32)
        self.fat_sectors = u16(bs, 22) or u32(bs, 36)
        root_sectors = (self.root_entries * 32 + self.bps - 1) // self.bps
        first_data = self.reserved + self.nfats * self.fat_sectors + root_sectors
        self.clusters = (self.total_sectors - first_data) // self.spc
        # The FAT type is defined by the number of clusters (Microsoft FAT specification).
        self.fat_type = 12 if self.clusters < 4085 else 16 if self.clusters < 65525 else 32
        self.cluster_size = self.bps * self.spc
        self.base = base
        self.fat_off = base + self.reserved * self.bps
        self.root_off = self.fat_off + self.nfats * self.fat_sectors * self.bps
        self.data_off = base + first_data * self.bps
        self.fs_end = base + self.total_sectors * self.bps
        if self.total_sectors > part.sectors or self.fs_end > len(img):
            raise AuditError("%s: the FAT filesystem is larger than its partition" % label)
        if self.fat_type == 32:
            self.volume_id, self.volume_label = u32(bs, 67), bs[71:82]
        else:
            self.volume_id, self.volume_label = u32(bs, 39), bs[43:54]
        fat_len = self.fat_sectors * self.bps
        self.fat = img[self.fat_off:self.fat_off + fat_len]
        for i in range(1, self.nfats):
            if img[self.fat_off + i * fat_len:self.fat_off + (i + 1) * fat_len] != self.fat:
                self.anomalies.append("FAT copy %d differs from FAT copy 1" % (i + 1))
        self.eoc = {12: 0xFF8, 16: 0xFFF8, 32: 0x0FFFFFF8}[self.fat_type]
        self.used = {}                 # cluster -> path that owns it
        self.volume_entry = None
        self.entries = {}
        self.slack = {}                # path -> bytes between the end of the file and the end of its last cluster
        self._read_tree()
        self._integrity_checks(part)

    def describe(self):
        return "FAT%d, %d-byte clusters, %d clusters, volume id 0x%08x, label %r" % (
            self.fat_type, self.cluster_size, self.clusters, self.volume_id,
            self.volume_label.decode("cp437").rstrip())

    # -- FAT table and cluster chains ------------------------------------------------
    def next_cluster(self, n):
        if self.fat_type == 16:
            return u16(self.fat, 2 * n)
        if self.fat_type == 32:
            return u32(self.fat, 4 * n) & 0x0FFFFFFF
        v = u16(self.fat, n + n // 2)            # FAT12: 12-bit entries, two per 3 bytes
        return v >> 4 if n & 1 else v & 0xFFF

    def chain(self, first, owner):
        """Follow a cluster chain, refusing loops, cross-links and out-of-range links."""
        out, c = [], first
        while True:
            if not 2 <= c < self.clusters + 2:
                self.anomalies.append("%s: cluster chain leaves the data area (cluster %d)" % (owner, c))
                break
            if c in self.used:
                self.anomalies.append("%s: cluster %d is also used by %s (cross-linked)" % (owner, c, self.used[c]))
                break
            self.used[c] = owner
            out.append(c)
            nxt = self.next_cluster(c)
            if nxt >= self.eoc:
                break
            if nxt in (0, self.eoc - 1):
                self.anomalies.append("%s: broken cluster chain after cluster %d" % (owner, c))
                break
            c = nxt
        return out

    def cluster_offset(self, c):
        return self.data_off + (c - 2) * self.cluster_size

    def read_chain(self, clusters):
        return b"".join(self.img[self.cluster_offset(c):self.cluster_offset(c) + self.cluster_size]
                        for c in clusters)

    # -- directories -----------------------------------------------------------------
    def _parse_dir(self, raw, dirpath):
        """Yield (name, attr, nt_flags, short, first_cluster, size, times) per entry."""
        lfn, lfn_sum, lfn_count, ended = {}, None, 0, False
        for i in range(0, len(raw) - 31, 32):
            e = raw[i:i + 32]
            if ended:
                if any(e):
                    self.anomalies.append("%s: data after the end-of-directory marker" % dirpath)
                    break
                continue
            if e[0] == 0x00:
                ended = True
                continue
            if e[0] == 0xE5:
                self.anomalies.append("%s: deleted directory entry %r" % (dirpath, bytes(e[1:11])))
                lfn = {}
                continue
            attr = e[11]
            if attr & 0x3F == 0x0F:                       # a long-file-name fragment
                seq = e[0] & 0x1F
                if e[0] & 0x40:                           # first fragment stored = last part of name
                    lfn, lfn_sum, lfn_count = {}, e[13], seq
                elif e[13] != lfn_sum:
                    self.anomalies.append("%s: orphaned long-file-name fragment" % dirpath)
                    lfn = {}
                    continue
                lfn[seq] = e[1:11] + e[14:26] + e[28:32]  # 13 UTF-16 characters
                continue
            short, nt = bytes(e[0:11]), e[12]
            name = short_name_text(short, nt)
            if lfn:
                if lfn_sum == lfn_checksum(short) and sorted(lfn) == list(range(1, lfn_count + 1)):
                    long_name = b"".join(lfn[k] for k in sorted(lfn)).decode("utf-16-le", "replace")
                    name = long_name.split("\x00")[0]
                else:
                    self.anomalies.append("%s: long file name does not belong to entry %s" % (dirpath, name))
                lfn = {}
            times = {"modified": fat_datetime(u16(e, 24), u16(e, 22)),
                     "created": fat_datetime(u16(e, 16), u16(e, 14), e[13]),
                     "accessed": fat_datetime(u16(e, 18))}
            first = u16(e, 26) | (u16(e, 20) << 16 if self.fat_type == 32 else 0)
            yield name, attr, nt, short, first, u32(e, 28), times

    def _read_tree(self):
        if self.fat_type == 32:
            root = self.read_chain(self.chain(u32(self.img, self.base + 44), "/"))
        else:
            root = self.img[self.root_off:self.root_off + self.root_entries * 32]
        self._walk(root, "", 0)

    def _walk(self, raw, dirpath, depth):
        if depth > 32:
            self.anomalies.append("%s: directories nested too deeply" % dirpath)
            return
        seen = set()
        for name, attr, nt, short, first, size, times in self._parse_dir(raw, dirpath or "/"):
            if attr & 0x08 and not attr & 0x10:           # the volume label pseudo-entry
                self.volume_entry = (short, times)
                continue
            if name in (".", ".."):
                continue
            path = dirpath + "/" + name
            if path.lower() in seen:
                self.anomalies.append("%s: duplicate name in directory" % path)
            seen.add(path.lower())
            if attr & 0x10:                               # sub-directory
                clusters = self.chain(first, path) if first else []
                if not clusters:
                    self.anomalies.append("%s: directory without clusters" % path)
                self.entries[path] = FatEntry(path, True, attr, 0, short, nt, times, b"")
                self._walk(self.read_chain(clusters), path, depth + 1)
                continue
            if size == 0:
                if first:
                    self.anomalies.append("%s: empty file owns clusters" % path)
                    self.chain(first, path)
                data = b""
            else:
                clusters = self.chain(first, path) if first else []
                need = (size + self.cluster_size - 1) // self.cluster_size
                if len(clusters) != need:
                    self.anomalies.append("%s: %d clusters allocated, %d needed for %d bytes"
                                          % (path, len(clusters), need, size))
                data = self.read_chain(clusters)[:size]
                if clusters:
                    # "Slack": the rest of the last cluster.  The firmware never reads it.
                    # mcopy leaves stale bytes of its copy buffer there (from the file
                    # copied before), so it is not zero, but it is deterministic; it is
                    # compared between the two images in audit_boot().
                    last = self.cluster_offset(clusters[-1])
                    used_in_last = size - (len(clusters) - 1) * self.cluster_size
                    self.slack[path] = self.img[last + used_in_last:last + self.cluster_size]
            self.entries[path] = FatEntry(path, False, attr, size, short, nt, times, data)

    def _integrity_checks(self, part):
        """Find data the firmware would never read but that should not be there."""
        lost, dirty_free = 0, 0
        for c in range(2, self.clusters + 2):
            v = self.next_cluster(c)
            if v != 0 and c not in self.used:
                lost += 1
            elif v == 0 and not is_zero(self.img, self.cluster_offset(c), self.cluster_offset(c) + self.cluster_size):
                dirty_free += 1
        if lost:
            self.anomalies.append("%d clusters are marked used but belong to no file (lost clusters)" % lost)
        if dirty_free:
            self.anomalies.append("%d free clusters contain non-zero data" % dirty_free)
        last_entry = (self.clusters + 2) * self.fat_type // 8
        if not is_zero(self.fat, align_up(last_entry, 4), len(self.fat)):
            self.anomalies.append("unused tail of the FAT table is not zero")
        if self.fat_type != 32 and not is_zero(self.img, self.base + self.bps, self.fat_off):
            self.anomalies.append("reserved sectors after the boot sector are not zero")
        data_end = self.cluster_offset(self.clusters + 2)
        part_end = (part.first_lba + part.sectors) * 512
        if not is_zero(self.img, data_end, part_end):
            self.anomalies.append("non-zero bytes after the last cluster of the filesystem")


def audit_boot(off_fs, cand_fs, rep):
    rep.section("[2] Boot partition (FAT filesystem): every file, sha256 of both images")
    rep.line("  official : " + off_fs.describe())
    rep.line("  candidate: " + cand_fs.describe())
    ok = True
    reserved_off = off_fs.img[off_fs.base:off_fs.fat_off]
    reserved_cand = cand_fs.img[cand_fs.base:cand_fs.fat_off]
    if reserved_off != reserved_cand:
        ok = False
        rep.os_problem("boot", "the FAT boot sector / reserved sectors differ (filesystem parameters)")
    for fs in (off_fs, cand_fs):
        for a in fs.anomalies:
            if fs is cand_fs:
                ok = False
                rep.os_problem("boot", "candidate filesystem: " + a)
            else:
                rep.warn("official filesystem: " + a)
    rep.line()
    # One line per file: marker, status, sha256, size, path.  "[!!]" marks every
    # difference that is not expected (anything except zImage).
    row = "  %-4s %-9s %-64s %9s  %s"
    rep.line(row % ("", "status", "sha256", "size", "path"))
    paths = sorted(set(off_fs.entries) | set(cand_fs.entries), key=lambda p: p.lower())
    n_same = n_files = 0
    timestamp_diffs = []
    zimage_differs = False
    for path in paths:
        a, b = off_fs.entries.get(path), cand_fs.entries.get(path)
        if a is None or b is None:
            e = a or b
            where = "only in official" if b is None else "only in candidate"
            rep.line(row % ("[!!]", "MISSING", e.sha256 or "(directory)", e.size, path + "  (" + where + ")"))
            rep.os_problem("boot", "%s: %s" % (path, where), show=False)
            ok = False
            continue
        if a.times != b.times:
            timestamp_diffs.append(path)
        if a.is_dir and b.is_dir:
            if a.meta() != b.meta():
                rep.os_problem("boot", "%s: directory metadata differs %s -> %s" % (path, a.meta(), b.meta()))
                ok = False
            continue
        n_files += 1
        if a.meta() == b.meta() and a.sha256 == b.sha256:
            n_same += 1
            rep.line(row % ("", "identical", a.sha256, a.size, path))
            continue
        meta_diff = {k: (a.meta()[k], b.meta()[k]) for k in a.meta() if a.meta()[k] != b.meta()[k]}
        expected = path == "/zImage" and set(meta_diff) <= {"size"}
        mark = "" if expected else "[!!]"
        rep.line(row % (mark, "DIFFERENT", a.sha256 or "-", a.size, path + "  (official)"))
        rep.line(row % (mark, "", b.sha256 or "-", b.size, path + "  (candidate)"))
        if expected:
            zimage_differs = True
            rep.line("       -> expected: zImage contains the kernel AND the whole root filesystem;"
                     " analysed in [3] and [4]")
            continue
        ok = False
        detail = "content differs" if a.sha256 != b.sha256 else ""
        if meta_diff:
            detail += (", " if detail else "") + ", ".join("%s %s -> %s" % (k, x, y) for k, (x, y) in meta_diff.items())
        rep.os_problem("boot", "%s: %s" % (path, detail), show=False)
    rep.line()
    rep.line("  %d of %d boot files identical%s." % (
        n_same, n_files, " (zImage differs, see [3]/[4])" if zimage_differs else ""))
    if timestamp_diffs:
        rep.warn("FAT timestamps differ for %s (timestamps are not executed; listed separately): %s"
                 % (plural(len(timestamp_diffs), "entry"), ", ".join(timestamp_diffs[:8])))
    if off_fs.volume_entry != cand_fs.volume_entry:
        rep.warn("the volume label entry (name or timestamp) differs")
    # Slack bytes (after the end of each file, never read by the firmware): mcopy fills
    # them deterministically, so apart from zImage itself they should be identical.
    slack_diff = [p for p in off_fs.slack if p != "/zImage" and p in cand_fs.slack
                  and off_fs.slack[p] != cand_fs.slack[p]]
    rep.line("  Slack bytes after the end of each file (not part of any file, never read): %s"
             % ("identical in both images for every file except zImage" if not slack_diff
                else "differ for %s" % plural(len(slack_diff), "file")))
    if slack_diff:
        rep.warn("slack bytes (after the end of the file, never read by the firmware) differ for: %s"
                 % ", ".join(slack_diff[:8]))
    others = [p for g, p in rep.os_problems if g == "boot"]
    if others:
        rep.line("  Boot partition differences outside zImage: %d (marked [!!] / DIFFERENT / MISSING above)" % len(others))
    rep.summary.append(("Boot partition (files other than zImage)",
                        "identical" if ok else "DIFFERENT (%d)" % len(others)))
    return ok


# --------------------------------------------------------------------------------------
# [3] Kernel: zImage container, decompressed kernel, embedded initramfs
# --------------------------------------------------------------------------------------

GNU_BUILD_ID_NOTE = b"\x04\x00\x00\x00\x14\x00\x00\x00\x03\x00\x00\x00GNU\x00"
PC_RELATIVE_BUDGET = 32     # genuine kernels need a handful (1 in the kernel, 7 in the decompressor)
OTHER_COMPRESSION_MAGICS = [(b"\xfd7zXZ\x00", "xz"), (b"\x5d\x00\x00", "lzma"), (b"BZh", "bzip2"),
                            (b"\x02\x21\x4c\x18", "lz4"), (b"\x28\xb5\x2f\xfd", "zstd"),
                            (b"\x89\x4c\x5a\x4f", "lzo")]


class Kernel:
    """Everything we need from one zImage.

    zImage layout (arch/arm/boot/compressed/vmlinux.lds.S):
        [decompressor code + data][gzip of the kernel 'Image' ("piggy")][GOT, padding]
    Kernel Image layout (arch/arm/kernel/vmlinux.lds.S), in file order:
        [code, read-only data, init code][init data ... initramfs blob][size word]
        [zero padding up to a page boundary][per-cpu data, .data ...]  (.bss follows in RAM)
    The initramfs blob is placed in the middle, so a different root filesystem moves
    everything after it, and every pointer to that later part changes by the same
    amount ("relocation").
    """

    def __init__(self, zimage, label):
        self.label, self.z = label, zimage
        if len(zimage) < 0x40 or u32(zimage, 0x24) != 0x016F2818:
            raise AuditError("%s: zImage has no ARM Linux zImage magic number" % label)
        self.z_start, self.z_end = u32(zimage, 0x28), u32(zimage, 0x2C)
        # Optional table described at 0x34/0x38: piggy size location, kernel .bss size ...
        self.piggy_size_addr = self.bss_size = None
        if u32(zimage, 0x34) == 0x45454545:
            t = u32(zimage, 0x38)
            if t + 16 <= len(zimage) and u32(zimage, t + 4) == 0x5A534C4B:
                self.piggy_size_addr, self.bss_size = u32(zimage, t + 8), u32(zimage, t + 12)
        # Locate the gzip-compressed kernel: the first gzip member that inflates fully.
        self.piggy_start = self.piggy_end = None
        for m in re.finditer(b"\x1f\x8b\x08", zimage):
            try:
                image, used = inflate_gzip(zimage, m.start())
            except (zlib.error, AuditError):
                continue
            if len(image) >= 1 << 20:
                self.piggy_start, self.piggy_end, self.image = m.start(), m.start() + used, image
                break
        if self.piggy_start is None:
            raise AuditError("%s: no gzip-compressed kernel found in zImage" % label)
        self.consistency = []
        if self.z_end != len(zimage):
            self.consistency.append("zImage header says the file is %d bytes, it is %d" % (self.z_end, len(zimage)))
        if self.piggy_size_addr is not None and self.piggy_size_addr != self.piggy_end - 4:
            self.consistency.append("zImage table does not point at the end of the compressed kernel")
        self._find_banner_and_config()
        self._find_initramfs()

    def _find_banner_and_config(self):
        m = re.search(rb"Linux version [\x20-\x7e]+", self.image)
        self.banner = m.group(0).decode() if m else "(no 'Linux version' banner found)"
        m = re.search(rb"#\d+ (?:SMP )?(?:PREEMPT\S* )?([A-Z][a-z]{2} [A-Z][a-z]{2} [ \d]\d \d\d:\d\d:\d\d [A-Z]{3,4} \d{4})",
                      self.banner.encode())
        self.build_time = m.group(1).decode() if m else None
        # Embedded kernel .config (CONFIG_IKCONFIG): gzip between the IKCFG_ST/IKCFG_ED markers.
        self.config = None
        s = self.image.find(b"IKCFG_ST")
        if s >= 0:
            try:
                self.config = inflate_gzip(self.image, s + 8, 16 << 20)[0].decode("utf-8", "replace")
            except (zlib.error, AuditError):
                pass
        self.page_offset = 0xC0000000
        if self.config:
            m = re.search(r"^CONFIG_PAGE_OFFSET=(0x[0-9a-fA-F]+)$", self.config, re.M)
            if m:
                self.page_offset = int(m.group(1), 16)
        # ARM kernels are linked at PAGE_OFFSET + TEXT_OFFSET (0x8000): the first byte of
        # the Image has this virtual address.
        self.va_base = self.page_offset + 0x8000

    def _find_initramfs(self):
        """The initramfs is a gzip member whose content starts with a cpio header.  The
        kernel stores its length in __initramfs_size, the next 8-byte aligned word after
        it (usr/initramfs_data.S); we check that this word agrees."""
        self.blob_start = None
        for m in re.finditer(b"\x1f\x8b\x08", self.image):
            d = zlib.decompressobj(16 + 15)
            try:
                head = d.decompress(self.image[m.start():m.start() + 65536], 6)
            except zlib.error:
                continue
            if head not in (b"070701", b"070702"):
                continue
            try:
                _, used = inflate_gzip(self.image, m.start())
            except (zlib.error, AuditError):
                continue
            self.blob_start = m.start()
            self.first_member_len = used
            break
        if self.blob_start is None:
            raise AuditError("%s: no initramfs (gzip-compressed cpio) found in the kernel" % self.label)
        # Find __initramfs_size: an 8-aligned word w at position p with p == align8(start + w).
        self.blob_len = None
        p = align_up(self.blob_start + self.first_member_len, 8)
        if p + 4 <= len(self.image) and u32(self.image, p) == self.first_member_len:
            self.blob_len = self.first_member_len
        else:
            # Not the simple case: search forward for a self-consistent size word.
            q = align_up(self.blob_start + 8, 8)
            while q + 4 <= len(self.image):
                w = u32(self.image, q)
                if w and align_up(self.blob_start + w, 8) == q:
                    self.blob_len = w
                    break
                q += 8
        if self.blob_len is None:
            # Something precedes or follows the gzip member inside the blob.  Carry on with
            # the member alone; the code comparison in [3] will then show what is wrong.
            self.consistency.append("no consistent __initramfs_size word found after the initramfs")
            self.blob_len = self.first_member_len
        self.blob_end = self.blob_start + self.blob_len
        self.size_word = align_up(self.blob_end, 8)
        # The part that follows the initramfs starts at a page boundary after zero padding.
        q = self.size_word + 4
        while q < len(self.image) and self.image[q] == 0:
            q += 1
        self.first_nonzero_after = q
        self.tail_start = q & ~0xFFF
        self.blob = self.image[self.blob_start:self.blob_end]


class ShiftRule:
    """'Values in [lo, hi) of the official image appear as value+delta in the candidate'."""

    def __init__(self, name, deltas, lo, hi):
        self.name, self.deltas, self.lo, self.hi = name, [d & MASK32 for d in deltas], lo, hi


def explain_word(x, y, word_addr, rules, base=None, pc_relative=True):
    """Is the change official word x -> candidate word y explained by relocation?

    The official value x must be a reference into a shifted region, read in one of the
    ways that occur in ARM kernels: an absolute address; an offset from the kernel start
    (only allowed where the caller passes `base`, i.e. inside the kallsyms table); or an
    address relative to the word's own position (PC-relative literals of hand-written
    assembly, allowing the +-4 KiB reach of an ARM 'ldr').  And y - x must be exactly the
    shift of that region.  Returns (label, kind) or None."""
    d = (y - x) & MASK32
    for r in rules:
        if d not in r.deltas:
            continue
        if r.lo <= x < r.hi:
            return r.name + ", absolute address", "absolute"
        if base is not None and r.lo <= (x + base) & MASK32 < r.hi:
            return r.name + ", kallsyms offset", "offset"
        t = (x + word_addr) & MASK32
        if pc_relative and r.lo - 4104 <= t < r.hi + 4104:
            return r.name + ", PC-relative", "pc"
    return None


def compare_words(a, b, a_off, b_off, length, rules, addr_of, excluded, counts, unexplained,
                  base_range=None, base=None, pc_relative=True, pc_words=None):
    """Compare a[a_off:a_off+length] with b[b_off:b_off+length] as 32-bit words.

    Every differing word must be explained by `rules` or lie in an `excluded` byte range
    (offsets relative to a_off, with a label).  "Offset from kernel start" readings are
    only accepted for words inside base_range (the kallsyms table); PC-relative readings
    only if pc_relative is True, and they are recorded in pc_words so that they can be
    listed.  Results go to counts (label -> n) and unexplained (list of (offset, x, y))."""
    block = 4096
    for blk in range(0, length, block):
        n = min(block, length - blk)
        if a[a_off + blk:a_off + blk + n] == b[b_off + blk:b_off + blk + n]:
            continue
        for i in range(blk, blk + n, 4):
            if i + 4 > length:                          # trailing bytes that are not a full word
                if a[a_off + i:a_off + length] != b[b_off + i:b_off + length]:
                    unexplained.append((a_off + i, None, None))
                break
            x, y = u32(a, a_off + i), u32(b, b_off + i)
            if x == y:
                continue
            label = None
            for lo, hi, what in excluded:
                if lo < i + 4 and i < hi:
                    label = what
                    break
            if label is None:
                in_table = base_range is not None and base_range[0] <= a_off + i < base_range[1]
                found = explain_word(x, y, addr_of(i), rules, base if in_table else None, pc_relative)
                if found:
                    label = found[0]
                    if found[1] == "pc" and pc_words is not None:
                        pc_words.append((a_off + i, x, y))
            if label is None:
                unexplained.append((a_off + i, x, y))       # offset in the official file
            else:
                counts[label] = counts.get(label, 0) + 1


def find_kallsyms_offsets(image, limit):
    """Locate kallsyms_offsets (CONFIG_KALLSYMS_BASE_RELATIVE): the longest run of
    non-decreasing 32-bit words that are all plausible offsets from the kernel start.
    Returns (start, end) byte offsets, or None."""
    n = len(image) // 4
    words = struct.unpack_from("<%dI" % n, image)
    best, start, prev = (0, 0), None, -1
    for i, x in enumerate(words):
        if x < limit and x >= prev:
            if start is None:
                start = i
            prev = x
            continue
        if start is not None and i - start > best[1] - best[0]:
            best = (start, i)
        start, prev = (i, x) if x < limit else (None, -1)
    if start is not None and n - start > best[1] - best[0]:
        best = (start, n)
    return (4 * best[0], 4 * best[1]) if best[1] - best[0] >= 1000 else None


def kallsyms_tables(image, start):
    """Parse the kallsyms tables of a 32-bit CONFIG_KALLSYMS_BASE_RELATIVE kernel (Linux
    5.15, scripts/kallsyms.c), given where kallsyms_offsets starts.  The tables follow
    each other, 4-byte aligned: offsets[n], relative_base, num_syms (= n), names (one
    length byte + that many token indices per symbol), markers[(n+255)/256],
    token_table (256 NUL-terminated strings), token_index[256].  Returns a dict, or
    None if the layout does not parse cleanly."""
    try:
        p = start + 4 * 1000                               # the first offsets can be 0, 0, ...
        while u32(image, p + 4) != (p - start) // 4:     # relative_base, then num_syms
            p += 4
            if (p - start) // 4 > 1 << 20:
                return None
        n = (p - start) // 4
        if n < 1000:
            return None
        offsets = struct.unpack_from("<%dI" % n, image, start)
        base = u32(image, p)
        q = names_start = p + 8
        entries = []
        for _ in range(n):
            ln = image[q]
            if ln == 0:
                return None
            entries.append((q, bytes(image[q:q + 1 + ln])))
            q += 1 + ln
        names_end = q
        markers = align_up(q, 4)
        q = token_table = markers + 4 * ((n + 255) // 256)
        tokens = []
        for _ in range(256):
            e = image.index(b"\x00", q)
            tokens.append(bytes(image[q:e]))
            q = e + 1
        token_index = align_up(q, 4)
        end = token_index + 512
        return dict(start=start, n=n, offsets=offsets, base=base, entries=entries,
                    names_start=names_start, names_end=names_end, markers=markers,
                    token_table=token_table, token_index=token_index, end=end, tokens=tokens)
    except (IndexError, ValueError, struct.error):
        return None


def kallsyms_name(t, raw):
    s = b"".join(t["tokens"][b] for b in raw[1:])
    return s[1:].decode("ascii", "replace")


def explain_kallsyms_reorders(A, B, start, rules, unexplained):
    """Symbols at the same address may be listed in either order in kallsyms (ties in the
    sort by address).  When the initramfs ends on an 8-byte boundary, __irf_end and
    __initramfs_size get the same address and can swap places in kallsyms_names.

    A difference in kallsyms_names is accepted only if: both kernels have the same
    tables (same symbol count, same layout, identical markers, token table and token
    index); the candidate's entries are the official ones with only runs of consecutive
    entries reordered; every symbol of a reordered run has ONE address in the candidate
    (so no name changes the address it stands for); and each symbol's official address
    maps to that address unchanged or by one of the relocation rules.  The unexplained
    words that lie entirely inside such runs are then explained.
    Returns (groups, still_unexplained); groups = [(address, [names])]."""
    ta, tb = kallsyms_tables(A, start), kallsyms_tables(B, start)
    if not ta or not tb:
        return [], unexplained
    if (ta["n"] != tb["n"] or ta["names_start"] != tb["names_start"] or ta["names_end"] != tb["names_end"]
            or ta["end"] != tb["end"] or A[ta["markers"]:ta["end"]] != B[tb["markers"]:tb["end"]]):
        return [], unexplained
    ea, eb = ta["entries"], tb["entries"]
    groups, spans = [], []
    i, n = 0, ta["n"]
    while i < n:
        if ea[i][1] == eb[i][1]:
            i += 1
            continue
        j = i
        while j < n and ea[j][1] != eb[j][1]:
            j += 1
        # Extend the run while the multisets still differ (a run may contain equal entries)
        while j < n and sorted(e[1] for e in ea[i:j]) != sorted(e[1] for e in eb[i:j]):
            j += 1
        if sorted(e[1] for e in ea[i:j]) != sorted(e[1] for e in eb[i:j]):
            return [], unexplained
        new_offsets = set(tb["offsets"][i:j])
        if len(new_offsets) != 1:
            return [], unexplained
        x = new_offsets.pop()
        target = (tb["base"] + x) & MASK32
        for k in range(i, j):
            old = (ta["base"] + ta["offsets"][k]) & MASK32
            d = (target - old) & MASK32
            if d and not any(d in r.deltas and r.lo <= old < r.hi for r in rules):
                return [], unexplained
        groups.append((target, [kallsyms_name(ta, e[1]) for e in ea[i:j]]))
        spans.append((ea[i][0], ea[j - 1][0] + len(ea[j - 1][1])))
        i = j
    if not spans:
        return [], unexplained
    rest = []
    for off, x, y in unexplained:
        diff = [off + b for b in range(4) if x is None or A[off + b] != B[off + b]]
        if x is not None and diff and all(any(lo <= d < hi for lo, hi in spans) for d in diff):
            continue
        rest.append((off, x, y))
    return groups, rest


def unchanged_lookalikes(a, b, a_off, b_off, length, rule):
    """Count words that look like absolute pointers into the shifted region but are equal
    in both images (constants that happen to fall into that address range)."""
    n = length // 4
    wa = struct.unpack_from("<%dI" % n, a, a_off)
    wb = struct.unpack_from("<%dI" % n, b, b_off)
    return [(a_off + 4 * i, x) for i, (x, y) in enumerate(zip(wa, wb)) if x == y and rule.lo <= x < rule.hi]


def first_difference(a, b):
    """Offset of the first differing byte (None if a == b)."""
    n = min(len(a), len(b))
    for blk in range(0, n, 65536):
        if a[blk:blk + 65536] != b[blk:blk + 65536]:
            for i in range(blk, min(blk + 65536, n)):
                if a[i] != b[i]:
                    return i
    return n if len(a) != len(b) else None


def config_diff(ca, cb, limit=40):
    la = [l for l in ca.splitlines() if l and not l.startswith("#") or l.startswith("# CONFIG")]
    lb = [l for l in cb.splitlines() if l and not l.startswith("#") or l.startswith("# CONFIG")]
    sa, sb = set(la), set(lb)
    return ["- " + l for l in la if l not in sb][:limit] + ["+ " + l for l in lb if l not in sa][:limit]


def audit_kernel(ka, kb, rep):
    rep.section("[3] Kernel: is the kernel code identical, apart from the embedded initramfs?")
    for k in (ka, kb):
        rep.line("  %-9s zImage %d bytes (sha256 %s)" % (k.label, len(k.z), sha256(k.z)))
        rep.line("            decompressor %d bytes, compressed kernel %d bytes, kernel Image %d bytes"
                 % (k.piggy_start, k.piggy_end - k.piggy_start, len(k.image)))
        rep.line("            initramfs blob at Image offset 0x%x, %d bytes; data after it: %d bytes"
                 % (k.blob_start, k.blob_len, len(k.image) - k.tail_start))
        rep.line("            banner: " + k.banner)
    ok = True
    for k in (ka, kb):
        for c in k.consistency:
            if k is kb:
                ok = False
                rep.os_problem("kernel", "candidate zImage: " + c)
            else:
                rep.warn("official zImage: " + c)
    if ka.banner == kb.banner:
        rep.line("  Kernel banner identical (same version, compiler, build timestamp).")
    else:
        rep.line("  Kernel banner DIFFERS.")
    if ka.config is not None and kb.config is not None:
        if ka.config == kb.config:
            rep.line("  Embedded kernel .config identical (%d bytes)." % len(ka.config))
        else:
            rep.line("  Embedded kernel .config DIFFERS:")
            for l in config_diff(ka.config, kb.config):
                rep.line("      " + l)

    # -- 3a. decompressor stub and the bytes after the compressed kernel ---------------
    counts, unexplained, problems, stub_pc = {}, [], [], []
    if ka.piggy_start != kb.piggy_start:
        problems.append("the decompressor code has a different size (%d vs %d bytes)" % (ka.piggy_start, kb.piggy_start))
    else:
        ea, eb = ka.piggy_end, kb.piggy_end
        deltas = [eb - ea, align_up(eb, 4) - align_up(ea, 4), align_up(eb, 8) - align_up(ea, 8), len(kb.z) - len(ka.z)]
        rules = [ShiftRule("address after the compressed kernel", deltas, ea - 8, len(ka.z) + (2 << 20))]
        compare_words(ka.z, kb.z, 0, 0, ka.piggy_start, rules, lambda i: i, [], counts, unexplained,
                      pc_words=stub_pc)
        ta, tb = align_up(ea, 4), align_up(eb, 4)
        la, lb = len(ka.z) - ta, len(kb.z) - tb
        n = min(la, lb)
        if not (is_zero(ka.z, ea, ta) and is_zero(kb.z, eb, tb)):
            problems.append("non-zero padding after the compressed kernel")
        compare_words(ka.z, kb.z, ta, tb, n - n % 4, rules, lambda i: ta + i, [], counts, unexplained,
                      pc_relative=False)
        if not (is_zero(ka.z, ta + n - n % 4, len(ka.z)) and is_zero(kb.z, tb + n - n % 4, len(kb.z))):
            problems.append("the data after the compressed kernel differs in size")
    if len(stub_pc) > PC_RELATIVE_BUDGET:
        problems.append("%d PC-relative explanations needed (more than %d): not accepted" % (len(stub_pc), PC_RELATIVE_BUDGET))
    stub_ok = not problems and not unexplained
    rep.sub("3a. zImage decompressor and trailer")
    for p in problems:
        rep.line("  " + p)
    for label, n in sorted(counts.items()):
        rep.line("  %6d words differ: %s (shift by the change of the compressed kernel size)" % (n, label))
    for off, x, y in stub_pc:
        rep.line("         PC-relative word at zImage offset 0x%x: %08x -> %08x" % (off, x, y))
    for off, x, y in unexplained[:20]:
        rep.line("  unexplained difference at zImage offset 0x%x: %s -> %s" % (
            off, "%08x" % x if x is not None else "?", "%08x" % y if y is not None else "?"))
    rep.line("  result: " + ("NOT identical" if not stub_ok else "identical modulo relocation" if counts
                             else "identical"))

    # -- 3b. the kernel Image (code and data) ----------------------------------------------
    rep.sub("3b. kernel Image (code and data outside the initramfs blob)")
    counts, unexplained, problems, image_pc, same = {}, [], [], [], []
    A, B = ka.image, kb.image
    if ka.blob_start != kb.blob_start:
        problems.append("the code and data before the initramfs have a different size "
                        "(initramfs at 0x%x vs 0x%x): a different kernel build" % (ka.blob_start, kb.blob_start))
    elif (kb.first_nonzero_after - ka.first_nonzero_after) % 4096:
        problems.append("the data after the initramfs is not shifted by whole pages")
    elif len(B) - kb.first_nonzero_after != len(A) - ka.first_nonzero_after:
        problems.append("the data after the initramfs has a different size (%d vs %d bytes)"
                        % (len(A) - ka.first_nonzero_after, len(B) - kb.first_nonzero_after))
    else:
        shift = kb.first_nonzero_after - ka.first_nonzero_after
        va = ka.va_base
        end_of_bss = va + len(A) + (ka.bss_size if ka.bss_size is not None else 16 << 20) + 0x10000
        rules = [ShiftRule("pointer into data after the initramfs", [shift], va + ka.tail_start, end_of_bss),
                 ShiftRule("pointer to __initramfs_size", [kb.size_word - ka.size_word], va + ka.size_word, va + ka.size_word + 4),
                 ShiftRule("pointer to the end of the initramfs", [kb.blob_end - ka.blob_end], va + ka.blob_end, va + ka.blob_end + 1)]
        excluded = []
        # "Offset from kernel start" values are legitimate in the kallsyms offset table only.
        ktab = find_kallsyms_offsets(A[:ka.blob_start], len(A) + (ka.bss_size or 0) + 0x10000)
        if ktab:
            rep.line("  kallsyms offset table found at Image offset 0x%x-0x%x (%d entries)"
                     % (ktab[0], ktab[1], (ktab[1] - ktab[0]) // 4))
        # The GNU build-id is a hash the linker computes over the whole kernel, initramfs
        # included, so it necessarily differs.  It is metadata: no code reads it to decide anything.
        ia, ib = A.find(GNU_BUILD_ID_NOTE, 0, ka.blob_start), B.find(GNU_BUILD_ID_NOTE, 0, kb.blob_start)
        if ia >= 0 and ia == ib:
            excluded.append((ia + 16, ia + 36, "GNU build-id (linker hash over the whole kernel, includes the initramfs)"))
        # If only the build timestamp differs (another SOURCE_DATE_EPOCH), allow exactly
        # those characters to differ, wherever the official string occurs (linux_banner
        # before the initramfs, init_uts_ns in .data after it).
        excluded_tail = []
        if ka.build_time and kb.build_time and ka.build_time != kb.build_time and len(ka.build_time) == len(kb.build_time):
            ta_, tb_ = ka.build_time.encode(), kb.build_time.encode()
            for m in re.finditer(re.escape(ta_), A[:ka.blob_start]):
                if B[m.start():m.end()] == tb_:
                    excluded.append((m.start(), m.end(), "kernel build timestamp string"))
            for m in re.finditer(re.escape(ta_), A[ka.tail_start:]):
                if B[kb.tail_start + m.start():kb.tail_start + m.end()] == tb_:
                    excluded_tail.append((m.start(), m.end(), "kernel build timestamp string"))
        compare_words(A, B, 0, 0, ka.blob_start, rules, lambda i: va + i, excluded, counts, unexplained,
                      base_range=ktab, base=va, pc_relative=True, pc_words=image_pc)
        if not (is_zero(A, ka.blob_end, ka.size_word) and is_zero(B, kb.blob_end, kb.size_word)):
            problems.append("non-zero bytes between the initramfs and __initramfs_size")
        if not (is_zero(A, ka.size_word + 4, ka.first_nonzero_after) and is_zero(B, kb.size_word + 4, kb.first_nonzero_after)):
            problems.append("non-zero bytes between __initramfs_size and the data after it")
        tail_len = len(A) - ka.tail_start
        # Inside the moved part only absolute pointers into it can change: PC-relative
        # references within the moved part move with it, and kallsyms is not there.
        compare_words(A, B, ka.tail_start, kb.tail_start, tail_len, rules,
                      lambda i: va + ka.tail_start + i, excluded_tail, counts, unexplained, pc_relative=False)
        if unexplained and ktab:
            before = len(unexplained)
            reordered, unexplained = explain_kallsyms_reorders(A, B, ktab[0], rules, unexplained)
            if reordered:
                counts["kallsyms names of symbols at the same address, listed in another order"] = \
                    before - len(unexplained)
            for address, names in reordered:
                rep.line("  kallsyms: %s - one address in the candidate (0x%08x), same names in another"
                         " order" % (", ".join(names), address))
        if len(image_pc) > PC_RELATIVE_BUDGET:
            problems.append("%d PC-relative explanations needed (more than %d): not accepted"
                            % (len(image_pc), PC_RELATIVE_BUDGET))
        # Transparency: words that look like pointers into the moved part but did not
        # change.  In a genuine relink these are constants that happen to fall into that
        # address range (77 such words between the official 0.8.5 and 0.8.6 kernels).
        if shift:
            same = unchanged_lookalikes(A, B, 0, 0, ka.blob_start, rules[0]) + \
                unchanged_lookalikes(A, B, ka.tail_start, kb.tail_start, tail_len, rules[0])
        if "kernel build timestamp string" in counts:
            rep.warn("the kernel build timestamp differs (%s vs %s): the candidate was built with another "
                     "SOURCE_DATE_EPOCH; the code is otherwise compared normally" % (ka.build_time, kb.build_time))
    for p in problems:
        rep.line("  " + p)
    for label, n in sorted(counts.items()):
        rep.line("  %6d words differ: %s" % (n, label))
    for off, x, y in image_pc:
        rep.line("         PC-relative word at Image offset 0x%x: %08x -> %08x" % (off, x, y))
    if same:
        rep.line("  %d words look like pointers into the moved part but are unchanged (constants that"
                 " happen to fall into that address range; listed with --verbose)" % len(same))
        if rep.verbose:
            for off, x in same:
                rep.line("         Image offset 0x%x: %08x" % (off, x))
    for off, x, y in unexplained[:20]:
        rep.line("  unexplained difference at Image offset 0x%x (address 0x%08x): %s -> %s" % (
            off, ka.va_base + off, "%08x" % x if x is not None else "?", "%08x" % y if y is not None else "?"))
    if len(unexplained) > 20:
        rep.line("  ... %d more unexplained differences" % (len(unexplained) - 20))
    image_ok = not problems and not unexplained
    if image_ok and not counts:
        rep.line("  result: identical - not a single byte differs outside the initramfs blob.")
    elif image_ok:
        rep.line("  result: identical modulo relocation - every differing word is explained above: a")
        rep.line("  reference into the part of the kernel that the initramfs moved, changed by exactly")
        rep.line("  the amount it moved, or the build-id / build timestamp, or (kallsyms) the names of")
        rep.line("  symbols that share one address listed in another order.")
    else:
        fd = first_difference(A, B)
        rep.line("  result: NOT proven identical.  Image sizes %d / %d bytes, first differing byte at"
                 " offset %s." % (len(A), len(B), "0x%x" % fd if fd is not None else "-"))
    if ka.z == kb.z:
        rep.line("  (the two zImage files are byte-identical)")
    kernel_ok = ok and stub_ok and image_ok
    if not kernel_ok:
        rep.os_problem("kernel", "the kernel code could not be proven identical to the official kernel")
        rep.line("  What to do: rebuild the OFFICIAL image from the same seedsigner-os checkout")
        rep.line("  (SS_ARGS=\"--pi0 --app-branch=<official tag>\" docker compose up --force-recreate --build)")
        rep.line("  and check that its sha256 equals the published one.  If it does, your build")
        rep.line("  environment reproduces the official kernel exactly.")
    rep.summary.append(("Kernel code (zImage minus initramfs)",
                        "identical modulo relocation" if kernel_ok else "NOT PROVEN IDENTICAL"))
    return kernel_ok


# --------------------------------------------------------------------------------------
# [4] cpio "newc" archives of the initramfs, unpacked the way the kernel does it
# --------------------------------------------------------------------------------------

S_IFMT = 0o170000
FILE_TYPES = {0o040000: "dir", 0o100000: "file", 0o120000: "symlink", 0o020000: "char-device",
              0o060000: "block-device", 0o010000: "fifo", 0o140000: "socket"}
HEX8 = re.compile(rb"[0-9A-Fa-f]{8}\Z")


class CpioEntry:
    def __init__(self, name, fields, data, order):
        (self.ino, self.mode, self.uid, self.gid, self.nlink, self.mtime, self.size,
         self.dev_major, self.dev_minor, self.rdev_major, self.rdev_minor) = fields
        self.name, self.data, self.order = name, data, order
        self.type = FILE_TYPES.get(self.mode & S_IFMT, "unknown(0o%o)" % (self.mode & S_IFMT))


class Node:
    """One path of the root filesystem as the kernel would create it."""

    def __init__(self, entry, data):
        self.type, self.perm = entry.type, entry.mode & 0o7777
        self.uid, self.gid, self.mtime, self.order = entry.uid, entry.gid, entry.mtime, entry.order
        self.data = data
        self.size = len(data) if entry.type == "file" else 0
        self.sha256 = sha256(data) if entry.type == "file" else None
        self.target = data.split(b"\x00")[0].decode("utf-8", "surrogateescape") if entry.type == "symlink" else None
        self.rdev = (entry.rdev_major, entry.rdev_minor) if "device" in entry.type else None
        self.links = None                       # tuple of all paths sharing this inode (hard links)

    def meta(self):
        """Everything that is compared, except the modification time."""
        m = {"type": self.type, "mode": "%04o" % self.perm, "uid/gid": "%d/%d" % (self.uid, self.gid)}
        if self.type == "file":
            m["size"], m["sha256"] = self.size, self.sha256
        if self.target is not None:
            m["symlink target"] = self.target
        if self.rdev is not None:
            m["device"] = "%d,%d" % self.rdev
        if self.links:
            m["hard links"] = ", ".join(self.links)
        return m


class RootFs:
    """Parse the initramfs blob exactly like init/initramfs.c: a sequence of cpio
    archives, uncompressed or compressed, separated by NUL padding; later entries win."""

    def __init__(self, blob, label):
        self.label, self.anomalies, self.entries, self.members = label, [], [], []
        pos = 0
        while pos < len(blob):
            if blob[pos] == 0:                               # padding between archives
                pos += 1
                continue
            if blob.startswith(b"0707", pos):                 # uncompressed archive(s)
                used = self._parse_stream(blob, pos, "uncompressed data at blob offset %d" % pos)
                self.members.append("uncompressed cpio at blob offset %d (%d bytes)" % (pos, used))
                if used <= 0:                                 # not a valid archive (anomaly recorded)
                    break
                pos += used
                continue
            if blob.startswith(b"\x1f\x8b", pos):              # gzip member
                try:
                    data, used = inflate_gzip(blob, pos)
                except (zlib.error, AuditError) as ex:
                    self.anomalies.append("corrupt gzip member at blob offset %d: %s" % (pos, ex))
                    break
                self.members.append("gzip member at blob offset %d (%d bytes -> %d bytes)" % (pos, used, len(data)))
                end = self._parse_stream(data, 0, "gzip member at blob offset %d" % pos)
                if not is_zero(data, end, len(data)):
                    self.anomalies.append("junk after the last archive inside the gzip member at %d" % pos)
                pos += used
                continue
            kind = next((name for magic, name in OTHER_COMPRESSION_MAGICS if blob.startswith(magic, pos)), None)
            self.anomalies.append("blob offset %d: %s that this tool cannot parse; "
                                  "the kernel may still unpack it" % (pos, ("%s-compressed data" % kind) if kind else "unknown data"))
            break
        self._build_tree()

    def _parse_stream(self, buf, pos, where):
        """Parse consecutive cpio archives starting at buf[pos]; returns bytes consumed."""
        start = pos
        while True:
            # one archive: entries until TRAILER!!!
            while True:
                if pos + 110 > len(buf):
                    self.anomalies.append("%s: truncated cpio header" % where)
                    return len(buf) - start
                hdr = buf[pos:pos + 110]
                magic = bytes(hdr[:6])
                if magic not in (b"070701", b"070702"):
                    self.anomalies.append("%s: no cpio magic at offset %d" % (where, pos))
                    return pos - start
                raw = [bytes(hdr[6 + 8 * i:14 + 8 * i]) for i in range(13)]
                if not all(HEX8.match(r) for r in raw):
                    self.anomalies.append("%s: malformed cpio header at offset %d" % (where, pos))
                    return pos - start
                f = [int(r, 16) for r in raw]
                namesize, check = f[11], f[12]
                name_raw = bytes(buf[pos + 110:pos + 110 + namesize])
                if namesize == 0 or len(name_raw) != namesize or name_raw[-1:] != b"\x00":
                    self.anomalies.append("%s: cpio name not NUL-terminated at offset %d" % (where, pos))
                    return pos - start
                name = name_raw.split(b"\x00")[0]
                if len(name) != namesize - 1:
                    self.anomalies.append("%s: cpio name %r contains a NUL byte" % (where, name_raw))
                dpos = align_up(pos + 110 + namesize, 4)
                size = f[6]
                if dpos + size > len(buf):
                    self.anomalies.append("%s: cpio data of %r truncated" % (where, name))
                    return len(buf) - start
                data = buf[dpos:dpos + size]
                pos = align_up(dpos + size, 4)
                if name == b"TRAILER!!!":
                    break
                if magic == b"070702" and sum(data) & MASK32 != check:
                    self.anomalies.append("%s: checksum error in %r" % (where, name))
                self.entries.append(CpioEntry(name.decode("utf-8", "surrogateescape"),
                                              f[:11], data, len(self.entries)))
            # after a trailer: NUL padding, then possibly another archive
            while pos < len(buf) and buf[pos] == 0:
                pos += 1
            if not buf.startswith(b"0707", pos):
                return pos - start

    @staticmethod
    def canonical(name):
        """'.' -> '/', 'a/b' -> '/a/b'.  Returns None for names that the kernel would
        resolve differently from how they read ('..', '//', leading '/', etc.)."""
        if name == ".":
            return "/"
        n = name[2:] if name.startswith("./") else name
        parts = n.split("/")
        if not n or any(p in ("", ".", "..") for p in parts):
            return None
        return "/" + n

    def _build_tree(self):
        self.tree = {}
        links = {}
        for e in self.entries:
            path = self.canonical(e.name)
            if path is None:
                self.anomalies.append("non-canonical name %r (could resolve to another path)" % e.name)
                continue
            if path in self.tree:
                self.anomalies.append("%s appears twice in the archive (the later entry wins)" % path)
            # Hard links: the kernel links regular files/devices with nlink >= 2 and the
            # same (device, inode) to the first such name.  newc stores the data once.
            if e.nlink >= 2 and e.type not in ("dir", "symlink"):
                links.setdefault((e.dev_major, e.dev_minor, e.ino, e.mode & S_IFMT), []).append((path, e))
            self.tree[path] = Node(e, e.data)
        for group in links.values():
            paths = tuple(sorted(p for p, _ in group))
            with_data = [e for _, e in group if e.size]
            data = with_data[-1].data if with_data else b""
            if len(with_data) > 1:
                self.anomalies.append("hard-linked files %s carry data more than once" % (paths,))
            for p, e in group:
                node = Node(e, data)
                node.links = paths
                self.tree[p] = node
        # Structural rules that every archive made by "find . | sort | cpio" satisfies:
        # each parent directory exists, comes earlier, and is a real directory (not a
        # symlink, through which a file could be written somewhere else).
        for path, node in self.tree.items():
            if path == "/":
                continue
            parent = path.rsplit("/", 1)[0] or "/"
            pnode = self.tree.get(parent)
            if parent != "/" and pnode is None:
                self.anomalies.append("%s: parent directory missing from the archive" % path)
            elif pnode is not None and pnode.type != "dir":
                self.anomalies.append("%s: parent %s is a %s, not a directory" % (path, parent, pnode.type))
            elif pnode is not None and pnode.order > node.order:
                self.anomalies.append("%s: appears before its parent directory" % path)


def in_app_dir(path):
    return path == APP_DIR or path.startswith(APP_DIR + "/")


def describe_diff(a, b):
    if a is None:
        return "only in candidate (%s)" % b.type
    if b is None:
        return "only in official (%s)" % a.type
    ma, mb = a.meta(), b.meta()
    parts = []
    for k in ma:
        if k in mb and ma[k] != mb[k]:
            if k == "sha256":
                parts.append("content differs (sha256 %s.. -> %s..)" % (ma[k][:16], mb[k][:16]))
            else:
                parts.append("%s %s -> %s" % (k, ma[k], mb[k]))
    for k in mb:
        if k not in ma:
            parts.append("%s: (none) -> %s" % (k, mb[k]))
    for k in ma:
        if k not in mb:
            parts.append("%s: %s -> (none)" % (k, ma[k]))
    return "; ".join(parts)


def audit_rootfs(off, cand, rep):
    rep.section("[4] Root filesystem (initramfs inside zImage): every entry compared")
    for r in (off, cand):
        rep.line("  %-9s %d entries; %s" % (r.label, len(r.entries), "; ".join(r.members)))
    for r in (off, cand):
        for a in r.anomalies:
            if r is cand:
                rep.os_problem("rootfs", "candidate archive: " + a)
            else:
                rep.warn("official archive: " + a)
    paths = sorted(set(off.tree) | set(cand.tree))
    same = {"os": 0, "app": 0}
    diffs = {"os": [], "app": []}
    mtimes = {"os": [], "app": []}
    for p in paths:
        a, b = off.tree.get(p), cand.tree.get(p)
        grp = "app" if in_app_dir(p) else "os"
        if a is not None and b is not None and a.meta() == b.meta():
            same[grp] += 1
            if a.mtime != b.mtime:
                mtimes[grp].append((p, a.mtime, b.mtime))
            continue
        diffs[grp].append((p, describe_diff(a, b)))
    rep.sub("4a. entries OUTSIDE %s" % APP_DIR)
    rep.line("  identical: %d   different / missing / extra: %d" % (same["os"], len(diffs["os"])))
    for d in diffs["os"]:
        rep.os_problem("rootfs", "%s: %s" % d, show=False)
    rep.capped(diffs["os"], lambda d: rep.line("  [!!] OUTSIDE /opt/src: %s: %s" % d), "differences")
    if not diffs["os"]:
        rep.line("  every entry outside %s is identical (type, mode, owner, size, sha256, link target, device)." % APP_DIR)
    rep.sub("4b. entries INSIDE %s (the application; analysed in [5])" % APP_DIR)
    rep.line("  identical: %d   different / missing / extra: %d" % (same["app"], len(diffs["app"])))
    for grp, title in (("os", "outside"), ("app", "inside")):
        if mtimes[grp]:
            ex = mtimes[grp][0]
            rep.warn("%s entries %s %s have a different mtime only (e.g. %s: %d -> %d). mtimes come from "
                     "buildroot's SOURCE_DATE_EPOCH and do not change what runs."
                     % (len(mtimes[grp]), title, APP_DIR, ex[0], ex[1], ex[2]))
    hist = {}
    for r in (off, cand):
        h = {}
        for n in r.tree.values():
            h[n.mtime] = h.get(n.mtime, 0) + 1
        hist[r.label] = sorted(h.items(), key=lambda kv: -kv[1])[:3]
    rep.line("  mtimes (most common): official %s; candidate %s" % (hist[off.label], hist[cand.label]))
    os_ok = not diffs["os"] and not cand.anomalies
    rep.summary.append(("Root filesystem outside %s" % APP_DIR,
                        "identical (%d entries)" % same["os"] if os_ok else
                        "DIFFERENT (%s%s)" % (plural(len(diffs["os"]), "entry"),
                                              ", archive anomalies" if cand.anomalies else "")))
    return diffs["app"]


# --------------------------------------------------------------------------------------
# [5] /opt/src: Python bytecode, translations, metadata, and the app source tree
# --------------------------------------------------------------------------------------

def siphash(k0, k1, data, c_rounds, d_rounds):
    """SipHash-c-d (Aumasson & Bernstein).  CPython uses it for hash-based .pyc files
    (Python/pyhash.c, _Py_KeyedHash): SipHash-1-3 since 3.11, SipHash-2-4 before.
    Written out here so that the check works with any Python version, not only with
    the one that compiled the image."""
    M = (1 << 64) - 1

    def rotl(x, b):
        return ((x << b) | (x >> (64 - b))) & M

    def rounds(n, v0, v1, v2, v3):
        for _ in range(n):
            v0 = (v0 + v1) & M; v1 = rotl(v1, 13) ^ v0; v0 = rotl(v0, 32)
            v2 = (v2 + v3) & M; v3 = rotl(v3, 16) ^ v2
            v0 = (v0 + v3) & M; v3 = rotl(v3, 21) ^ v0
            v2 = (v2 + v1) & M; v1 = rotl(v1, 17) ^ v2; v2 = rotl(v2, 32)
        return v0, v1, v2, v3

    v0, v1 = k0 ^ 0x736F6D6570736575, k1 ^ 0x646F72616E646F6D
    v2, v3 = k0 ^ 0x6C7967656E657261, k1 ^ 0x7465646279746573
    n = len(data)
    full = n - n % 8
    for i in range(0, full, 8):                       # message blocks, little-endian
        m = int.from_bytes(data[i:i + 8], "little")
        v3 ^= m
        v0, v1, v2, v3 = rounds(c_rounds, v0, v1, v2, v3)
        v0 ^= m
    b = ((n & 0xFF) << 56) | int.from_bytes(data[full:], "little")   # last block + length
    v3 ^= b
    v0, v1, v2, v3 = rounds(c_rounds, v0, v1, v2, v3)
    v0 ^= b
    v2 ^= 0xFF                                        # finalisation
    v0, v1, v2, v3 = rounds(d_rounds, v0, v1, v2, v3)
    return ((v0 ^ v1 ^ v2 ^ v3) & M).to_bytes(8, "little")


def siphash_selftest():
    """Published SipHash-2-4 test vector (key 00..0f, message 00..0e)."""
    k = bytes(range(16))
    out = siphash(int.from_bytes(k[:8], "little"), int.from_bytes(k[8:], "little"), bytes(range(15)), 2, 4)
    return out == bytes.fromhex("e545be4961ca29a1")


def pyc_source_hash(magic, source, variant):
    """What importlib.util.source_hash(source) returns on the Python that wrote `magic`:
    SipHash keyed with the pyc's 4-byte magic number (variant = (c, d) rounds)."""
    return siphash(int.from_bytes(magic, "little"), 0, source, variant[0], variant[1])


PYTHON_MAGIC_NAMES = {3413: "3.8", 3425: "3.9", 3439: "3.10", 3495: "3.11", 3531: "3.12", 3571: "3.13"}


class MarshalReader:
    """A small, side-effect-free reader for CPython's marshal format (version 4) as used in
    .pyc files of CPython 3.11-3.13.  It never creates code objects and never executes
    anything; it returns a plain, comparable description of the content.  Strings and
    interned strings are treated alike, frozensets are compared as sets (their order
    depends on hash randomisation), and back-references are resolved, so two pycs
    compare equal exactly when they contain the same code."""

    CODE_FIELDS = ("argcount", "posonlyargcount", "kwonlyargcount", "stacksize", "flags",
                   "code", "consts", "names", "localsplusnames", "localspluskinds",
                   "filename", "name", "qualname", "firstlineno", "linetable", "exceptiontable")

    def __init__(self, data, drop_linetable):
        self.data, self.pos, self.refs, self.depth = data, 0, [], 0
        self.drop_linetable = drop_linetable

    def take(self, n):
        if n < 0 or self.pos + n > len(self.data):
            raise ValueError("truncated marshal data")
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def i32(self):
        return struct.unpack("<i", self.take(4))[0]

    def read(self):
        self.depth += 1
        if self.depth > 200:
            raise ValueError("marshal data nested too deeply")
        code = self.take(1)[0]
        flag, t = code & 0x80, chr(code & 0x7F)
        if t == "r":                                  # back-reference
            i = self.i32()
            if not 0 <= i < len(self.refs) or self.refs[i] is None:
                raise ValueError("bad marshal back-reference")
            self.depth -= 1
            return self.refs[i]
        idx = None
        if flag:                                      # CPython reserves the slot first
            idx = len(self.refs)
            self.refs.append(None)
        v = self.read_value(t)
        if idx is not None:
            self.refs[idx] = v
        self.depth -= 1
        return v

    def read_value(self, t):
        simple = {"N": ("None",), "F": ("bool", False), "T": ("bool", True),
                  "S": ("StopIteration",), ".": ("Ellipsis",)}
        if t in simple:
            return simple[t]
        if t == "i":
            return ("int", self.i32())
        if t == "l":
            n = self.i32()
            digits = struct.unpack("<%dH" % abs(n), self.take(2 * abs(n)))
            v = sum(d << (15 * k) for k, d in enumerate(digits))
            return ("int", -v if n < 0 else v)
        if t == "g":
            return ("float", self.take(8))
        if t == "y":
            return ("complex", self.take(16))
        if t == "s":
            return ("bytes", self.take(self.i32()))
        if t in "tu":
            return ("str", self.take(self.i32()).decode("utf-8", "surrogatepass"))
        if t in "aA":
            return ("str", self.take(self.i32()).decode("latin-1"))
        if t in "zZ":
            return ("str", self.take(self.take(1)[0]).decode("latin-1"))
        if t in "()[<>":
            n = self.take(1)[0] if t == ")" else self.i32()
            items = tuple(self.read() for _ in range(n))
            if t in "<>":
                return ("set" if t == "<" else "frozenset", tuple(sorted(items, key=repr)))
            return ("tuple" if t in "()" else "list", items)
        if t == "{":
            items = []
            while True:
                if self.data[self.pos:self.pos + 1] == b"0":
                    self.pos += 1
                    break
                items.append((self.read(), self.read()))
            return ("dict", tuple(sorted(items, key=repr)))
        if t == "c":
            fields = []
            for name in self.CODE_FIELDS:
                if name in ("argcount", "posonlyargcount", "kwonlyargcount", "stacksize", "flags", "firstlineno"):
                    value = ("int", self.i32())
                else:
                    value = self.read()
                if not (self.drop_linetable and name == "linetable"):
                    fields.append((name, value))
            return ("code", tuple(fields))
        raise ValueError("unsupported marshal type %r" % t)


def pyc_structure(pyc, drop_linetable):
    r = MarshalReader(pyc, drop_linetable)
    r.pos = 16
    v = r.read()
    if r.pos != len(pyc):
        raise ValueError("trailing bytes after the code object")
    return v


def pyc_filename(pyc):
    """co_filename of the module's code object (the path used at compile time)."""
    v = pyc_structure(pyc, True)
    return dict(v[1])["filename"][1]


def parse_mo(data):
    """Read a GNU gettext .mo catalog into {msgid: msgstr} (both bytes)."""
    if data[:4] == b"\xde\x12\x04\x95":
        fmt = "<"
    elif data[:4] == b"\x95\x04\x12\xde":
        fmt = ">"
    else:
        raise ValueError("not a .mo file")
    _, n, orig, trans = struct.unpack_from(fmt + "4I", data, 4)
    out = {}
    for i in range(n):
        lo, oo = struct.unpack_from(fmt + "2I", data, orig + 8 * i)
        lt, ot = struct.unpack_from(fmt + "2I", data, trans + 8 * i)
        out[bytes(data[oo:oo + lo])] = bytes(data[ot:ot + lt])
    return out


def kind_of(path):
    """What produced a file in /opt/src (see seedsigner-os opt/build.sh and post-build.sh)."""
    if "/__pycache__/" in path or path.endswith("/__pycache__"):
        return "bytecode"
    if path == EGG_INFO_DIR or path.startswith(EGG_INFO_DIR + "/"):
        return "metadata"
    if path.startswith(TRANSLATIONS_DIR + "/") or path == TRANSLATIONS_DIR:
        if path.endswith(".mo"):
            return "translation"
        if path.endswith(".ttf"):
            return "font-subset"
        return "submodule"
    if path.endswith(".py"):
        return "source"
    return "resource"


KIND_LEGEND = [
    ("source", "file copied verbatim from the app repository (git clone of the branch)"),
    ("resource", "non-Python file copied verbatim from the app repository"),
    ("bytecode", "__pycache__/*.pyc written at build time by compileall (checked-hash)"),
    ("translation", "messages.mo compiled from the pinned seedsigner-translations .po files by Babel "
                    "(PyPI, unpinned version; the version is written into the .mo header)"),
    ("font-subset", "NotoSans*-Regular.ttf cut down by pyftsubset (fonttools from PyPI, unpinned version)"),
    ("submodule", "other file of the pinned seedsigner-translations submodule"),
    ("metadata", "seedsigner.egg-info/* written by 'pip install -e .' (setuptools from PyPI, unpinned)"),
]


class AppTree:
    """The reviewed application source, read from a directory (and git, if available)."""

    ARTIFACT_DIRS = {"__pycache__", ".pytest_cache", ".git"}

    def __init__(self, root, rev, rep):
        self.root, self.files, self.git_info, self.gitlinks = root, {}, None, []
        self.all_paths = None          # every file of the repository (for SOURCES.txt)
        src = os.path.join(root, "src")
        if not os.path.isdir(src):
            raise AuditError("--app-tree %s: no src/ directory" % root)
        if self._from_git(rev, rep):
            return
        # Fallback: walk the working tree, skipping build/test artefacts.
        self.all_paths = set()
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in self.ARTIFACT_DIRS and not d.endswith(".egg-info")]
            for fn in filenames:
                self.all_paths.add(os.path.relpath(os.path.join(dirpath, fn), root).replace(os.sep, "/"))
        for dirpath, dirnames, filenames in os.walk(src):
            dirnames[:] = sorted(d for d in dirnames if d not in self.ARTIFACT_DIRS and not d.endswith(".egg-info"))
            for fn in sorted(filenames):
                if fn.endswith(".pyc") or fn == ".DS_Store":
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, src).replace(os.sep, "/")
                if os.path.islink(full):
                    self.files[rel] = ("symlink", os.readlink(full).encode(), None)
                else:
                    with open(full, "rb") as f:
                        data = f.read()
                    self.files[rel] = ("file", data, bool(os.stat(full).st_mode & 0o111))

    @staticmethod
    def _git_env():
        # Ignore GIT_DIR & co. from the caller's environment; never take optional locks.
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_OPTIONAL_LOCKS="0", LC_ALL="C")
        return env

    def _git(self, *args, **kw):
        return subprocess.run(["git", "-C", self.root] + list(args), capture_output=True,
                              env=self._git_env(), check=True, **kw).stdout

    def _from_git(self, rev, rep):
        """Use the committed tree of `rev`: its blob ids are what the build clones."""
        try:
            top = self._git("rev-parse", "--show-toplevel").decode().strip()
            if os.path.realpath(top) != os.path.realpath(self.root):
                if rev != "HEAD":
                    raise AuditError("--app-tree %s is not the top of a git repository (that is %s), so "
                                     "--app-rev %s cannot be checked" % (self.root, top, rev))
                rep.warn("%s is not the top of a git repository (that is %s); comparing the working-tree "
                         "files instead" % (self.root, top))
                return False
            commit = self._git("rev-parse", "--verify", rev + "^{commit}").decode().strip()
            tree = self._git("rev-parse", commit + "^{tree}").decode().strip()
            srctree = self._git("rev-parse", commit + ":src").decode().strip()
            listing = self._git("ls-tree", "-r", "-z", "--full-tree", commit, "src")
            names = self._git("ls-tree", "-r", "-z", "--name-only", "--full-tree", commit)
        except (OSError, subprocess.CalledProcessError) as ex:
            msg = getattr(ex, "stderr", b"") or str(ex).encode()
            detail = msg.decode("utf-8", "replace").strip().splitlines()[-1] if msg else str(ex)
            if rev != "HEAD":
                # A revision was asked for (e.g. a release tag): never fall back silently to
                # whatever files happen to be checked out.
                raise AuditError("git could not resolve --app-rev %s in %s (%s)" % (rev, self.root, detail))
            rep.warn("git could not read %s (%s); comparing the working-tree files instead" % (self.root, detail))
            return False
        self.git_info = (rev, commit, tree, srctree)
        self.all_paths = set(n.decode("utf-8", "surrogateescape") for n in names.split(b"\x00") if n)
        objects = []
        for rec in listing.split(b"\x00"):
            if not rec:
                continue
            meta, path = rec.split(b"\t", 1)
            mode, otype, oid = meta.decode().split()
            rel = path.decode("utf-8", "surrogateescape")[len("src/"):]
            if otype == "commit":
                self.gitlinks.append((rel, oid))
            else:
                objects.append((rel, mode, oid))
        # Read the blobs in one batch (git cat-file --batch is read-only).
        if objects:
            out, pos = self._git("cat-file", "--batch", input=b"".join(o.encode() + b"\n" for _, _, o in objects)), 0
            for rel, mode, oid in objects:
                header_end = out.index(b"\n", pos)
                size = int(out[pos:header_end].split()[2])
                data = out[header_end + 1:header_end + 1 + size]
                pos = header_end + 1 + size + 1
                if git_blob_id(data) != oid:
                    raise AuditError("git returned a blob that does not match its id")
                if mode == "120000":
                    self.files[rel] = ("symlink", data, None)
                else:
                    self.files[rel] = ("file", data, mode == "100755")
        return True


PYC_PATH = re.compile(r"^(.*)/__pycache__/([^/]+?)\.([a-z]+-\d+(?:\.opt-\d)?)\.pyc$")
MARSHAL_LAYOUT_OK = (3495, 3531, 3571)          # code-object layout understood by MarshalReader


def audit_bytecode(otree, ctree, rep):
    """Check every compiled Python file under /opt/src of the candidate.

    The build runs 'compileall -f --invalidation-mode=checked-hash' on /opt/src
    (post-build.sh).  Each __pycache__/X.cpython-312.pyc then starts with a 16-byte
    header: the magic number (Python version), flags (3 = hash-based, checked) and a
    SipHash of the X.py it was compiled from.  At import time Python uses the pyc only
    if that hash matches X.py, but it never checks that the bytecode BODY really is the
    compilation of X.py.  So we check both:
      header: magic and flags as in the official image, and hash == hash(X.py in the image);
      body:   if X.py is unchanged, the pyc must be byte-identical to the official pyc;
              otherwise X.py is recompiled with this Python (possible only if it has the
              same bytecode version) and the result is compared."""
    rep.sub("5b. Python bytecode (__pycache__/*.pyc) against the .py next to it")
    pycs_off = {p: n for p, n in otree.items() if in_app_dir(p) and p.endswith(".pyc") and n.type == "file"}
    if not pycs_off:
        rep.warn("the official image has no .pyc files in /opt/src; bytecode checks skipped")
        return 0
    # Reference values taken from the official image (most common magic / flags / tag).
    def most_common(values):
        counts = {}
        for v in values:
            counts[v] = counts.get(v, 0) + 1
        return max(counts, key=counts.get)
    magic = most_common(bytes(n.data[:4]) for n in pycs_off.values())
    flags = most_common(u32(n.data, 4) for n in pycs_off.values())
    tag = most_common(PYC_PATH.match(p).group(3) for p in pycs_off if PYC_PATH.match(p))
    num = u16(magic, 0)
    pyver = PYTHON_MAGIC_NAMES.get(num, "with magic %d" % num)
    rep.line("  official bytecode: magic %s = CPython %s, flags %d (%s), file tag '%s'"
             % (magic.hex(), pyver, flags, "hash-based, checked" if flags == 3 else
                "hash-based, unchecked" if flags == 1 else "timestamp-based", tag))

    # Which SipHash variant wrote the source hashes?  Decided on the official image, where
    # every pyc must match its .py: SipHash-1-3 for CPython >= 3.11, SipHash-2-4 before.
    pairs = []
    for p, n in pycs_off.items():
        m = PYC_PATH.match(p)
        s = otree.get(m.group(1) + "/" + m.group(2) + ".py") if m else None
        if s is not None and s.type == "file" and u32(n.data, 4) & 1:
            pairs.append((bytes(n.data[8:16]), bytes(s.data)))
    variant, best = (1, 3), -1
    for v in ((1, 3), (2, 4)):
        ok = sum(1 for h, src in pairs if pyc_source_hash(magic, src, v) == h)
        if ok > best:
            variant, best = v, ok
    hash_check = flags & 1 and pairs and best == len(pairs) and siphash_selftest()
    if hash_check:
        rep.line("  source-hash check calibrated: SipHash-%d-%d reproduces all %d official pyc headers"
                 % (variant[0], variant[1], len(pairs)))
    else:
        rep.warn("the source hashes of the official pycs could not be reproduced (%d of %d); "
                 "the header check against the .py is skipped" % (max(best, 0), len(pairs)))

    # Can this Python recompile the sources to the same bytecode version?
    if num not in MARSHAL_LAYOUT_OK:
        why_not = "this tool recompiles only CPython 3.11-3.13 bytecode"
    elif importlib.util.MAGIC_NUMBER != magic or sys.implementation.name != "cpython":
        why_not = "needs CPython %s" % pyver
    else:
        why_not = None
    can_compile = why_not is None
    prefix = None
    if can_compile:
        # co_filename inside each pyc is the path compileall saw at build time
        # (e.g. /output/target/opt/src/...); reuse it so recompiled files match.
        for p, n in sorted(pycs_off.items()):
            m = PYC_PATH.match(p)
            if not m:
                continue
            src = m.group(1) + "/" + m.group(2) + ".py"
            try:
                fn = pyc_filename(bytes(n.data))
            except (ValueError, struct.error, IndexError, KeyError):
                continue
            if fn.endswith(src):
                prefix = fn[:-len(src)]
                break
        can_compile = prefix is not None
        if not can_compile:
            why_not = "the compile-time path was not found in the official pycs"
    rep.line("  this Python: %s %s -> %s" % (
        sys.implementation.name, sys.version.split()[0],
        "can recompile the sources and compare the bytecode" if can_compile else
        "cannot recompile (%s); changed files get the header check only" % why_not))

    def compare_recompiled(pyc, source, image_path):
        """'exact', 'bytecode' (same code, other line-number table) or 'different'."""
        # Keep the path string referenced while marshalling, as compileall does: marshal
        # sets a "reference" flag on objects that have more than one reference, so the
        # bytes are only identical if the path is still alive here.
        filename = prefix + image_path
        code = compile(source, filename, "exec", dont_inherit=True, optimize=0)
        mine = magic + struct.pack("<I", flags) + importlib.util.source_hash(source) + marshal.dumps(code)
        del filename
        if mine == bytes(pyc):
            return "exact"
        try:
            if pyc_structure(bytes(pyc), True) == pyc_structure(mine, True):
                return "bytecode"
        except (ValueError, struct.error, IndexError):
            pass
        return "different"

    recompile_trusted = False
    if can_compile:
        # Calibration: does this Python reproduce the official bytecode?
        calib = {"exact": 0, "bytecode": 0, "different": 0}
        for p, n in pycs_off.items():
            m = PYC_PATH.match(p)
            src = m.group(1) + "/" + m.group(2) + ".py" if m else None
            s = otree.get(src) if src else None
            if s is not None and s.type == "file":
                calib[compare_recompiled(n.data, bytes(s.data), src)] += 1
        rep.line("  calibration on the official image: %d pyc reproduced byte-for-byte, %d with identical bytecode"
                 " but another line-number table, %d different" % (calib["exact"], calib["bytecode"], calib["different"]))
        if calib["bytecode"]:
            rep.line("    (line-number tables changed between CPython patch releases; the image was compiled by the")
            rep.line("    buildroot host Python - CPython 3.12.10 for SeedSigner 0.8.7 - which reproduces them exactly)")
        recompile_trusted = calib["different"] == 0
        if not recompile_trusted:
            rep.warn("this Python does not reproduce all official bytecode; recompilation results are not used")

    results = {"identical": [], "exact": [], "bytecode": [], "header": []}
    problems = []
    for p, n in sorted(ctree.items()):
        if not in_app_dir(p) or n.type != "file":
            continue
        if not (p.endswith((".pyc", ".pyo", ".so")) or ".cpython-" in p):
            continue
        m = PYC_PATH.match(p)
        if not m:
            problems.append("%s: compiled Python file outside __pycache__ (importable without its source)" % p)
            continue
        if m.group(3) != tag:
            problems.append("%s: bytecode for another interpreter or optimisation level" % p)
            continue
        srcpath = m.group(1) + "/" + m.group(2) + ".py"
        s = ctree.get(srcpath)
        if s is None or s.type != "file":
            problems.append("%s: no %s next to it (orphan bytecode)" % (p, srcpath))
            continue
        pyc, source = bytes(n.data), bytes(s.data)
        if len(pyc) < 16 or pyc[:4] != magic or u32(pyc, 4) != flags:
            problems.append("%s: header (magic/flags) differs from the official bytecode" % p)
            continue
        if hash_check and pyc[8:16] != pyc_source_hash(magic, source, variant):
            problems.append("%s: was NOT compiled from the %s in the image (source hash mismatch)" % (p, srcpath))
            continue
        o, osrc = otree.get(p), otree.get(srcpath)
        if osrc is not None and osrc.sha256 == s.sha256 and o is not None:
            if o.sha256 == n.sha256:
                results["identical"].append(p)
            else:
                problems.append("%s: its source is unchanged but the bytecode differs from the official one" % p)
            continue
        if recompile_trusted:
            r = compare_recompiled(pyc, source, srcpath)
            if r == "different":
                problems.append("%s: bytecode does NOT match its source (recompiled with this Python)" % p)
            else:
                results[r].append(p)
        else:
            results["header"].append(p)
    for text in problems:
        rep.app_problem(text, show=False)
    rep.capped(problems, lambda t: rep.line("  [FAIL] " + t), "failures")
    texts = {"identical": "byte-identical to the official image (their .py is unchanged)",
             "exact": "of changed/new sources: reproduced byte-for-byte by recompiling the .py",
             "bytecode": "of changed/new sources: same bytecode as recompiling the .py (line-number table differs)",
             "header": "of changed/new sources: header checked (compiled from this .py), body NOT verified"}
    for k, v in results.items():
        if v:
            rep.line("  %4d pyc %s" % (len(v), texts[k]))
            if k != "identical":
                rep.capped(v, lambda p: rep.line("         " + p), "files")
    if results["header"]:
        rep.incomplete.append("%d pyc bodies not verified (%s)" % (len(results["header"]), why_not))
        rep.warn("%d pyc bodies were not verified (%s); run the tool with CPython %s, e.g. "
                 "'docker run --rm -v \"$PWD\":/w -w /w python:%s python3 ssmax_image_audit.py ...'"
                 % (len(results["header"]), why_not, pyver, pyver))
    return sum(len(v) for v in results.values())


def audit_app(off, cand, app_diffs, app_tree, rep):
    rep.section("[5] /opt/src: the application directory")
    rep.line("  Legend (what produced each kind of file):")
    for k, text in KIND_LEGEND:
        rep.line("    %-12s %s" % (k, text))
    rep.line("  Removed by build.sh, so never in the image: tests/, docs/, tools/, l10n/, .github/, setup.*,")
    rep.line("  pyproject.toml, README.md, the .po files and .git* of the translations submodule.")
    otree, ctree = off.tree, cand.tree

    # -- 5a. list every difference inside /opt/src, with its kind ----------------------------
    rep.sub("5a. differences inside /opt/src (official -> candidate)")
    by_kind = {}
    for path, text in app_diffs:
        by_kind.setdefault(kind_of(path), []).append((path, text))
    for k, _ in KIND_LEGEND:
        items = by_kind.get(k, [])
        if items:
            rep.line("  [%s] %s:" % (k, plural(len(items), "difference")))
            rep.capped(items, lambda d: rep.line("      %s: %s" % d), "differences")
    if not app_diffs:
        rep.line("  none: /opt/src is identical to the official image.")
    # Owner and permissions: the build gives every app file the same few combinations
    # (rsync --chmod=u=rwX,go=rX, then chown 0:0).  Anything else (setuid bit, other owner,
    # symlink, device node, hard link) cannot come from the normal build.
    allowed = set((n.type, n.perm, n.uid, n.gid) for p, n in otree.items() if in_app_dir(p))
    odd = ["%s: %s mode %04o uid/gid %d/%d%s" % (p, n.type, n.perm, n.uid, n.gid, " (hard link)" if n.links else "")
           for p, n in sorted(ctree.items()) if in_app_dir(p)
           and ((n.type, n.perm, n.uid, n.gid) not in allowed or n.links)]
    for text in odd:
        rep.app_problem("unusual type/owner/permissions in /opt/src (not produced by the build): " + text, show=False)
    rep.capped(odd, lambda t: rep.line("  [FAIL] unusual type/owner/permissions: " + t), "entries")
    if not odd:
        rep.line("  types, owners and permissions in /opt/src: only the combinations the official build uses %s"
                 % sorted("%s %04o %d/%d" % a for a in allowed))

    # -- 5b. Python bytecode ----------------------------------------------------------
    pyc_total = audit_bytecode(otree, ctree, rep)

    # -- 5c. translations (.mo) and fonts ----------------------------------------------
    # The .mo catalogs and the font subsets are generated during the build by tools that
    # build.sh installs from PyPI without a version pin (Babel, fonttools).  A different
    # tool version can change the bytes without changing the content, so the catalogs are
    # compared message by message.  With --app-tree the translations must be the official
    # ones (same pinned submodule), so a change of messages counts as a failure there.
    mo_diff = [p for p, _ in app_diffs if p.endswith(".mo")]
    ttf_diff = [p for p, _ in app_diffs if kind_of(p) == "font-subset"]
    if mo_diff or ttf_diff:
        rep.sub("5c. generated translation catalogs and fonts")

    def mo_issue(text):
        if app_tree is not None:
            rep.app_problem(text + " (the app tree should pin the official translations)")
        else:
            rep.line("  " + text)

    for p in mo_diff:
        a, b = otree.get(p), ctree.get(p)
        if a is None or b is None:
            mo_issue("%s: translation catalog %s" % (p, "added" if a is None else "missing"))
            continue
        try:
            ca, cb = parse_mo(a.data), parse_mo(b.data)
        except (ValueError, struct.error) as ex:
            rep.app_problem("%s: cannot parse (%s)" % (p, ex))
            continue
        ha = ca.pop(b"", b"").decode("utf-8", "replace").splitlines()
        hb = cb.pop(b"", b"").decode("utf-8", "replace").splitlines()
        if ca != cb:
            changed = sum(1 for k in set(ca) | set(cb) if ca.get(k) != cb.get(k))
            mo_issue("%s: %d translated messages differ" % (p, changed))
        else:
            hd = ["'%s' -> '%s'" % (x, y) for x, y in zip(ha, hb) if x != y] + ha[len(hb):] + hb[len(ha):]
            rep.line("  %s: same %d messages; only the header differs: %s" % (p, len(ca), "; ".join(hd) or "(order)"))
    for p in ttf_diff:
        rep.warn("%s: subset font differs (it depends on the fonttools version used at build time; "
                 "fonts are data, not code)" % p)

    # -- 5d. egg-info metadata ---------------------------------------------------------
    # 'pip install -e .' (build.sh) leaves src/seedsigner.egg-info in the image.  Python on
    # the device does not need it.  SOURCES.txt lists the files of the repository known to
    # setuptools (src/ and tests/test*.py), so it changes whenever files are added.
    meta_diff = [p for p, _ in app_diffs if kind_of(p) == "metadata"
                 and not (otree.get(p) or ctree.get(p)).type == "dir"]
    if meta_diff:
        rep.sub("5d. seedsigner.egg-info (written by 'pip install -e .'; not used by the device)")
    for p in meta_diff:
        a, b = otree.get(p), ctree.get(p)
        if a is None or b is None:
            rep.line("  %s: only in %s" % (p, "candidate" if a is None else "official"))
            continue
        la, lb = a.data.decode("utf-8", "replace").splitlines(), b.data.decode("utf-8", "replace").splitlines()
        added = [l for l in lb if l not in set(la)]
        removed = [l for l in la if l not in set(lb)]
        rep.line("  %s: %d lines added, %d removed" % (p, len(added), len(removed)))
        for l in removed[:40]:
            rep.line("      - " + l)
        for l in added[:40]:
            rep.line("      + " + l)
        if p.endswith("/SOURCES.txt") and app_tree is not None and app_tree.all_paths is not None:
            bad = [l for l in added if l not in app_tree.all_paths and not l.startswith("src/seedsigner.egg-info/")]
            bad += [l for l in removed if l in app_tree.all_paths]
            if bad:
                rep.warn("SOURCES.txt changes that do not match the files of the app tree: %s" % ", ".join(bad[:10]))
            else:
                rep.line("      (every added/removed line matches a file added/removed in the app tree)")

    # -- 5e. the reviewed app source tree --------------------------------------------------
    if app_tree is None:
        rep.line()
        rep.line("  (no --app-tree given: the files above are not compared with the reviewed source)")
        rep.incomplete.append("no --app-tree: /opt/src was not compared with any source tree")
        state = "FAILED (%d)" % len(rep.app_problems) if rep.app_problems else \
            "INCOMPLETE" if rep.incomplete else "passed"
        rep.summary.append(("/opt/src checks (no --app-tree)", "%s; %d differences, %d pyc checked"
                            % (state, len(app_diffs), pyc_total)))
        return
    rep.sub("5e. /opt/src against the app tree %s" % app_tree.root)
    if app_tree.git_info:
        rev, commit, tree, srctree = app_tree.git_info
        rep.line("  git %s = commit %s" % (rev, commit))
        rep.line("        tree %s   (src/ subtree %s)" % (tree, srctree))
        rep.line("  -> compare the tree id with the one you reviewed; the build clones this committed tree.")
        for rel, oid in app_tree.gitlinks:
            rep.line("  submodule src/%s pinned at %s (fetched from GitHub by the build)" % (rel, oid))
    else:
        rep.line("  (working-tree files; make sure they are the reviewed commit: git status, git rev-parse HEAD^{tree})")
    sub_prefixes = [APP_DIR + "/" + rel + "/" for rel, _ in app_tree.gitlinks] or [TRANSLATIONS_DIR + "/"]
    matched, changed_vs_official = set(), []
    n_ok = 0
    for rel, (ftype, data, executable) in sorted(app_tree.files.items()):
        ipath = APP_DIR + "/" + rel
        if any(ipath.startswith(sp) for sp in sub_prefixes):
            continue                                   # submodule content is checked below
        node = ctree.get(ipath)
        matched.add(ipath)
        if node is None:
            rep.app_problem("%s: in the app tree but not in the image" % ipath)
            continue
        if ftype == "symlink":
            if node.type != "symlink" or node.target.encode("utf-8", "surrogateescape") != data:
                rep.app_problem("%s: symlink does not match the app tree" % ipath)
            continue
        if node.type != "file" or bytes(node.data) != data:
            rep.app_problem("%s: content differs from the app tree" % ipath)
            continue
        if executable is not None and bool(node.perm & 0o100) != executable:
            rep.app_problem("%s: executable bit differs from the app tree" % ipath)
            continue
        n_ok += 1
        o = otree.get(ipath)
        if o is None or o.sha256 != node.sha256:
            changed_vs_official.append(ipath + ("  (new)" if o is None else "  (modified)"))
    rep.line("  %d app files in the image are byte-identical to the app tree." % n_ok)
    rep.line("  %d of them differ from the official image (the reviewed modification)%s"
             % (len(changed_vs_official), ":" if changed_vs_official else "."))
    rep.capped(changed_vs_official, lambda p: rep.line("      " + p), "files")
    unexpected = []
    for p, n in sorted(ctree.items()):
        if not in_app_dir(p) or p in matched or p == APP_DIR:
            continue
        k = kind_of(p)
        if n.type == "dir":
            if any(m.startswith(p + "/") for m in matched) or k in ("bytecode", "metadata") \
                    or any((p + "/").startswith(sp) or sp.startswith(p + "/") for sp in sub_prefixes):
                continue
            unexpected.append(p)
            continue
        if k == "bytecode":
            continue                                   # verified in 5b
        if k in ("translation", "font-subset", "submodule"):
            o = otree.get(p)
            if o is None:
                unexpected.append(p + "  (submodule file not in the official image)")
            elif o.meta() != n.meta() and k == "submodule":
                unexpected.append(p + "  (submodule file differs from the official image)")
            continue                                   # .mo/.ttf differences: see 5c
        if k == "metadata":
            continue                                   # see 5d
        unexpected.append(p)
    for p in unexpected:
        rep.app_problem("%s: in the image but not explained by the app tree or the build" % p)
    for p, n in sorted(otree.items()):
        if in_app_dir(p) and p not in ctree and kind_of(p) in ("translation", "font-subset", "submodule"):
            rep.app_problem("%s: generated/submodule file missing from the candidate" % p)
    if not unexpected:
        rep.line("  every other file in /opt/src is explained: bytecode (5b), translations/fonts (5c),")
        rep.line("  egg-info (5d), or unchanged files of the pinned translations submodule.")
    rep.summary.append(("/opt/src vs app tree", "FAILED (%d)" % len(rep.app_problems) if rep.app_problems
                        else "matches, but INCOMPLETE (see notes)" if rep.incomplete else "matches"))


# --------------------------------------------------------------------------------------
# Main program
# --------------------------------------------------------------------------------------

def read_image(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as ex:
        raise AuditError("cannot read %s: %s" % (path, ex))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Compare a self-built SeedSigner OS image with the official image, file by file, "
                    "and prove that only /opt/src differs.")
    ap.add_argument("official", help="the official image (verify its signature first)")
    ap.add_argument("candidate", help="the image to audit")
    ap.add_argument("--app-tree", metavar="DIR", help="checkout of the reviewed app repository; every file "
                    "in /opt/src is then checked against DIR/src")
    ap.add_argument("--app-rev", metavar="REV", default="HEAD",
                    help="with --app-tree: git revision to compare with (default HEAD; uses git if installed)")
    ap.add_argument("--verbose", "-v", action="store_true", help="list everything, do not shorten lists")
    ap.add_argument("--max-list", type=int, default=60, metavar="N", help="longest list printed per group (default 60)")
    args = ap.parse_args(argv)
    rep = Report(args.verbose, args.max_list)

    print("ssmax_image_audit %s  (Python %s)" % (TOOL_VERSION, sys.version.split()[0]))
    try:
        imgs = {}
        for label, path in (("official", args.official), ("candidate", args.candidate)):
            imgs[label] = read_image(path)
            print("  %-9s %s  (%d bytes, sha256 %s)" % (label, path, len(imgs[label]), sha256(imgs[label])))
        off_img, cand_img = imgs["official"], imgs["candidate"]
        app_tree = AppTree(args.app_tree, args.app_rev, rep) if args.app_tree else None

        off_parts, cand_parts = parse_mbr(off_img, "official"), parse_mbr(cand_img, "candidate")
        audit_disk_layout(off_img, cand_img, off_parts, cand_parts, rep)

        off_fs = FatFilesystem(off_img, fat_partition(off_parts, off_img, "official"), "official")
        cand_fs = FatFilesystem(cand_img, fat_partition(cand_parts, cand_img, "candidate"), "candidate")
        audit_boot(off_fs, cand_fs, rep)

        kernels = {}
        for label, fs in (("official", off_fs), ("candidate", cand_fs)):
            z = fs.entries.get("/zImage")
            if z is None or z.is_dir:
                raise AuditError("%s: no /zImage on the boot partition" % label)
            kernels[label] = Kernel(z.data, label)
        audit_kernel(kernels["official"], kernels["candidate"], rep)

        off_rfs = RootFs(kernels["official"].blob, "official")
        cand_rfs = RootFs(kernels["candidate"].blob, "candidate")
        app_diffs = audit_rootfs(off_rfs, cand_rfs, rep)
        audit_app(off_rfs, cand_rfs, app_diffs, app_tree, rep)
    except AuditError as ex:
        print("\nERROR: %s" % ex)
        return 2

    rep.section("[6] Summary")
    for label, result in rep.summary:
        print("  %-42s %s" % (label, result))
    if rep.warnings:
        print()
        print("  Notes / warnings (do not affect the result unless stated):")
        for w in rep.warnings:
            print("   - " + w)
    print()
    if rep.os_problems:
        groups = {}
        for g, _ in rep.os_problems:
            groups[g] = groups.get(g, 0) + 1
        banner = ["DIFFERENCES OUTSIDE /opt/src:"]
        banner += ["  %s: %d" % (Report.GROUP_TITLES[g], n) for g, n in groups.items()]
        banner += ["The OS part of the candidate is NOT proven identical to the",
                   "official image.  Every such item is marked [!!] above."]
        print("#" * 79)
        for text in banner:
            print("##  %-71s##" % text)
        print("#" * 79)
        return 1
    print("  RESULT: the OS part is identical to the official image (boot files except zImage")
    print("  identical, kernel code identical modulo relocation, every root-filesystem entry")
    print("  outside /opt/src identical).")
    if rep.app_problems:
        print("  BUT %d check(s) on /opt/src FAILED (marked [FAIL] above)." % len(rep.app_problems))
        return 3
    if rep.incomplete:
        print("  /opt/src: no check failed, but the verification is INCOMPLETE:")
        for text in rep.incomplete:
            print("    - " + text)
        return 4
    print("  /opt/src: all checks passed%s." % ("" if app_tree else " (no --app-tree comparison requested)"))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                                    # a bug in this tool, not a finding
        import traceback
        traceback.print_exc()
        print("\nINTERNAL ERROR in ssmax_image_audit.py: no conclusion can be drawn (exit status 2).")
        sys.exit(2)
