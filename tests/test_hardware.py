from akeno.core.models import GpuInfo, HardwareSupport, Vendor
from akeno.hardware import gpu_db
from akeno.hardware.detect import HardwareService, merge


def test_identifies_nvidia_rtx_by_device_id():
    g = gpu_db.classify(GpuInfo(name="NVIDIA GeForce RTX 3080", vendor_id=0x10DE, device_id=0x2206))
    assert g.vendor == Vendor.NVIDIA
    assert g.architecture == "Ampere" and g.chip == "GA102"
    assert g.generation == "RTX 30" and g.has_tensor_cores


def test_device_id_wins_over_misleading_name_and_name_fallback():
    g = gpu_db.classify(GpuInfo(name="Some OEM Graphics", vendor_id=0x10DE, device_id=0x2782))
    assert g.architecture == "Ada Lovelace" and g.generation == "RTX 40"
    g2 = gpu_db.classify(GpuInfo(name="NVIDIA GeForce RTX 5070", vendor_id=0x10DE, device_id=0x9999))
    assert g2.generation == "RTX 50" and g2.chip is None


def test_gtx_turing_has_no_tensor_cores_and_is_unsupported():
    g = gpu_db.classify(GpuInfo(name="NVIDIA GeForce GTX 1660 SUPER", vendor_id=0x10DE, device_id=0x21C4))
    assert g.architecture == "Turing" and g.has_tensor_cores is False and g.generation is None
    assert gpu_db.nr_hardware_support(g).level == HardwareSupport.UNSUPPORTED


def test_amd_intel_software_unsupported():
    for name, vid in (("AMD Radeon RX 7900 XTX", 0x1002), ("Intel(R) Arc(TM) A770", 0x8086)):
        g = gpu_db.classify(GpuInfo(name=name, vendor_id=vid, device_id=1))
        assert gpu_db.nr_hardware_support(g).level == HardwareSupport.UNSUPPORTED
    sw = gpu_db.classify(GpuInfo(name="Microsoft Basic Render Driver", vendor_id=0x1414, device_id=0x8C, is_software=True))
    assert gpu_db.nr_hardware_support(sw).reason_code == "software"


def test_support_levels_never_claim_official():
    for dev, expected in ((0x2B85, HardwareSupport.COMMUNITY), (0x2684, HardwareSupport.EXPERIMENTAL),
                          (0x1E87, HardwareSupport.EXPERIMENTAL)):
        g = gpu_db.classify(GpuInfo(name="x", vendor_id=0x10DE, device_id=dev))
        assert gpu_db.nr_hardware_support(g).level == expected
    assert gpu_db.nr_hardware_support(None).level == HardwareSupport.UNKNOWN
    unk = gpu_db.classify(GpuInfo(name="NVIDIA Mystery", vendor_id=0x10DE, device_id=0x0001))
    assert gpu_db.nr_hardware_support(unk).level == HardwareSupport.UNKNOWN


def test_driver_version_conversion():
    assert gpu_db.nvidia_driver_from_windows_version("32.0.15.6109") == "561.09"
    assert gpu_db.nvidia_driver_from_windows_version("32.0.16.1714") == "617.14"
    assert gpu_db.nvidia_driver_from_windows_version(None) is None
    assert gpu_db.nvidia_driver_from_windows_version("1.2") is None


def test_multi_gpu_merge_and_primary_selection():
    dxgi = [
        {"name": "Intel(R) UHD Graphics 770", "vendor_id": 0x8086, "device_id": 0x4680, "vram_bytes": 128 << 20,
         "luid": "0:1", "index": 0, "driver_version_raw": "31.0.101.4502"},
        {"name": "NVIDIA GeForce RTX 4070", "vendor_id": 0x10DE, "device_id": 0x2786, "vram_bytes": 4 << 30,
         "luid": "0:2", "index": 1, "driver_version_raw": "32.0.15.6109", "feature_level": "12_2"},
        {"name": "Microsoft Basic Render Driver", "vendor_id": 0x1414, "device_id": 0x8C, "luid": "0:3",
         "index": 2, "is_software": True},
    ]
    reg = [{"name": "NVIDIA GeForce RTX 4070", "vendor_id": 0x10DE, "device_id": 0x2786, "vram_bytes": 12 << 30,
            "driver_version_raw": "32.0.15.6109", "driver_date": "1-16-2025"}]
    gpus = merge(dxgi, reg, {"driver_version": "561.09"})
    assert len(gpus) == 3
    nv = gpus[1]
    assert nv.vram_bytes == 12 << 30, "registry qwMemorySize must correct capped DXGI VRAM"
    assert nv.driver_date == "1-16-2025" and nv.driver_version == "561.09"
    assert gpus[0].driver_version == "31.0.101.4502"
    assert gpu_db.pick_primary(gpus) is nv


def test_failed_probe_is_reported_not_fatal():
    def boom():
        raise OSError("dxgi unavailable")
    svc = HardwareService({"dxgi": boom, "registry": lambda: [
        {"name": "NVIDIA GeForce RTX 2080", "vendor_id": 0x10DE, "device_id": 0x1E87, "vram_bytes": 8 << 30}],
        "os": lambda: {"os_name": "Windows 10 Pro"}})
    info = svc.detect()
    assert info.gpus and info.gpus[0].generation == "RTX 20"
    assert any("dxgi" in e for e in info.detection_errors)


def test_non_windows_reports_unavailable():
    info = HardwareService({}).detect()
    assert info.gpus == [] and info.detection_errors
