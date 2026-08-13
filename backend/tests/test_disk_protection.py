"""The restore must refuse to flash the disk the orchestrator boots from.

There is no undo. `format_and_restore` repartitions and mkfs's whatever device
it is handed, and the orchestrator holds the borg repository — the fleet's only
copy of every backup. Getting this check wrong once ends the deployment.

The check used to strip digits out of the host's root device name and ask
whether the result appeared anywhere inside the target path. That is wrong in
both directions, and the eMMC case is the one that matters: "/dev/mmcblk0p1"
becomes "/dev/mmcblkp", which is a substring of nothing, so the shield passed
and the restore proceeded onto the host's own disk.
"""
import pytest

from core.disk_ops import base_disk


@pytest.mark.parametrize("device,expected", [
    # NVMe: the partition suffix is p<N>, and the disk name itself ends in a
    # digit, which is what defeats "strip trailing digits".
    ("/dev/nvme0n1p2", "/dev/nvme0n1"),
    ("/dev/nvme0n1", "/dev/nvme0n1"),
    ("/dev/nvme10n1p15", "/dev/nvme10n1"),
    # eMMC: same shape, and the case the old code got wrong.
    ("/dev/mmcblk0p1", "/dev/mmcblk0"),
    ("/dev/mmcblk0", "/dev/mmcblk0"),
    # SCSI/SATA/virtio: plain trailing digits.
    ("/dev/sda1", "/dev/sda"),
    ("/dev/sda", "/dev/sda"),
    ("/dev/sdaa2", "/dev/sdaa"),
    ("/dev/vdb3", "/dev/vdb"),
    # Unrecognised shapes come back untouched, so a comparison fails closed
    # rather than matching on a mangled name.
    ("/dev/mapper/vg0-root", "/dev/mapper/vg0-root"),
    ("", ""),
])
def test_base_disk(device, expected):
    assert base_disk(device) == expected


def test_an_emmc_root_and_its_own_partition_resolve_to_the_same_disk():
    """The regression that motivated this. Under the old digit-stripping
    heuristic these two produced "/dev/mmcblkp" and "/dev/mmcblk0", the shield
    saw no match, and the restore wiped the orchestrator."""
    assert base_disk("/dev/mmcblk0p1") == base_disk("/dev/mmcblk0")


def test_a_neighbouring_disk_is_not_confused_with_the_host_root():
    """The other direction: sda must not swallow sdaa.

    Substring matching blocked this one, which is merely a restore that
    wrongly failed — but it is the same broken comparison.
    """
    assert base_disk("/dev/sdaa1") != base_disk("/dev/sda1")
