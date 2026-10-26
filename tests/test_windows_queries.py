"""The real Windows queries behind System Doctor and Network Lab, on the Windows runner (Windows PowerShell 5.1).

Stand-in providers cannot catch a script Windows PowerShell refuses to parse, so these run the real ones and only
check that Windows answered (the values depend on the machine)."""

import pytest

from akeno.paths import is_windows

pytestmark = pytest.mark.skipif(not is_windows(), reason="Windows PowerShell queries")


def test_a_multi_line_script_hands_its_result_to_json():
    from akeno.system.commands import powershell_json
    assert powershell_json("$a = 1\n[pscustomobject]@{a=$a}\n") == {"a": 1}


def test_drive_health_query_answers():
    from akeno.system.doctor.providers import LocalProbe
    data = LocalProbe().physical_disks()
    assert isinstance(data["disks"], list) and isinstance(data["smart"], list)
    assert data["disks"] or data["smart"] or data["error"], "Windows reported no drive at all"


def test_performance_counters_query_answers():
    from akeno.system.doctor.providers import LocalProbe
    assert isinstance(LocalProbe().perf(), dict)


def test_network_details_query_answers():
    from akeno.system.network import info
    assert isinstance(info.windows_details(), list)
