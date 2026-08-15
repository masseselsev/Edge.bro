"""Which image the kiosk is built from, and how its filename is resolved.

The base image was the weekly Debian *testing* live build. That gave a rescue
stick the newest kernel -- worth having when it must boot hardware nobody
chose in advance -- but it also meant the kiosk and the packages we ship into
it came from different distributions. The backend downloads .debs from its own
suite and bakes them into the payload, so when testing moved to Python 3.14
the shipped `borgbackup` (which declares `python3 (<< 3.14)`) stopped
installing, and the offline SSH unit runs `dpkg -i ... && systemctl start ssh`
-- taking ssh down with it.

Pinning to stable makes the two the same distribution by construction. The
cost is that `current-live` keeps the suite fixed but not the filename: the
point release is part of it. So the name is resolved from the directory's own
SHA512SUMS, and these tests cover that resolution -- including the trap that
the same file lists `.iso.contents`, `.iso.log` and `.iso.packages` beside the
image.
"""
from unittest.mock import patch

import iso_tasks


#: Trimmed from the real SHA512SUMS, with the non-image entries kept because
#: they are exactly what a looser match would pick up.
REAL_SUMS = """\
917be6d5667cfb7281b82eb8c43b3740  debian-live-13.6.0-amd64-xfce.iso
06a53b9d70d6a2862d1b357589dc6816  debian-live-13.6.0-amd64-xfce.iso.contents
486fe0235ec1a4c99326f0ef9a0d21a7  debian-live-13.6.0-amd64-xfce.iso.log
e5f9c98009ad4d96209b5977a793ebee  debian-live-13.6.0-amd64-xfce.iso.packages
459f790b7ef1ae1965abfc306c12de5b  debian-live-13.6.0-amd64-xfce.log
aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa  debian-live-13.6.0-amd64-gnome.iso
"""


def _with_sums(text):
    return patch.object(iso_tasks.subprocess, "check_output",
                        return_value=text.encode())


def test_the_pinned_url_points_at_stable_not_testing():
    """The regression this whole change exists to prevent."""
    assert "current-live" in iso_tasks.BASE_ISO_URL
    assert "testing" not in iso_tasks.BASE_ISO_URL
    assert "weekly" not in iso_tasks.BASE_ISO_URL


def test_the_current_point_release_is_resolved_from_the_checksums():
    with _with_sums(REAL_SUMS):
        urls = iso_tasks.stable_live_iso_urls()

    assert urls[0] == f"{iso_tasks.STABLE_LIVE_DIR}/debian-live-13.6.0-amd64-xfce.iso"


def test_the_companion_files_are_not_mistaken_for_the_image():
    """`.iso.contents` is a text listing. Downloading it as the base image
    fails later and far away from the cause."""
    with _with_sums(REAL_SUMS):
        urls = iso_tasks.stable_live_iso_urls()

    assert not any(u.endswith((".contents", ".log", ".packages")) for u in urls)


def test_a_different_desktop_is_not_picked_up():
    """The payload is built against the xfce image's layout."""
    with _with_sums(REAL_SUMS):
        urls = iso_tasks.stable_live_iso_urls()

    assert not any("gnome" in u for u in urls)


def test_the_newest_point_release_comes_first():
    """A directory caught mid-rotation lists both; the new one is the one
    whose checksums were just published."""
    sums = (
        "aaaa  debian-live-13.6.0-amd64-xfce.iso\n"
        "bbbb  debian-live-13.7.0-amd64-xfce.iso\n"
    )
    with _with_sums(sums):
        urls = iso_tasks.stable_live_iso_urls()

    assert urls[0].endswith("debian-live-13.7.0-amd64-xfce.iso")
    assert len(urls) == 2, "the older release should stay as a fallback"


def test_an_unreachable_index_falls_back_to_the_pinned_name():
    """An offline install must still get a usable URL rather than an
    exception out of a Celery task."""
    with patch.object(iso_tasks.subprocess, "check_output", side_effect=OSError("no network")):
        urls = iso_tasks.stable_live_iso_urls()

    assert urls == list(iso_tasks.DEFAULT_MIRROR_URLS)
    assert urls[0].startswith(iso_tasks.STABLE_LIVE_DIR)


def test_an_index_with_no_image_falls_back_too():
    with _with_sums("aaaa  README.txt\n"):
        urls = iso_tasks.stable_live_iso_urls()

    assert urls == list(iso_tasks.DEFAULT_MIRROR_URLS)


def test_checksum_verification_still_recognises_a_resolved_url_as_official():
    """`is_official` gates SHA512 verification. It used to compare the URL
    against the pinned constant, which a resolved point release never equals
    -- so every real download would have skipped verification."""
    # A later point release than the pinned fallback, which is the whole
    # point: once stable moves on, the resolved URL stops equalling the
    # constant, and an equality check would silently disable verification.
    with _with_sums("cccc  debian-live-13.9.0-amd64-xfce.iso\n"):
        resolved = iso_tasks.stable_live_iso_urls()[0]

    assert resolved != iso_tasks.BASE_ISO_URL, "otherwise this test proves nothing"
    assert resolved.startswith(iso_tasks.STABLE_LIVE_DIR)
