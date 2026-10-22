"""Rendering contracts. Names denote implementations, not interchangeable DLL collections."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Recipe:
    id: str
    name: str
    apis: tuple[str, ...]
    architectures: tuple[str, ...]
    native_calls: bool
    consumer: str | None
    components: tuple[str, ...]
    inputs: tuple[str, ...]
    success: str
    logs: tuple[str, ...] = ()


RECIPES = (
    Recipe('native_optiscaler', 'Native DLSS + OptiScaler NR', ('d3d12','d3d11','vulkan'), ('x64',), True,
           'optiscaler_nr', ('optiscaler','forwarder','nr_runtime'), ('color','depth','motion_vectors'),
           'Fresh Feature 18 create and successful evaluate results bound to this process and runtime.', ('OptiScaler.log',)),
    Recipe('native_renodx', 'Native DLSS + ReShade / RenoDX NR', ('d3d12',), ('x64',), True,
           'renodx_nr', ('reshade','renodx_nr','nr_runtime'), ('native_dlss_color','native_dlss_depth','native_dlss_motion'),
           'Selected NR consumer reports Feature 18 creation and successful evaluation.', ('ReShade.log',)),
    Recipe('feeder', 'DLSS5-Feeder + selected NR consumer', ('d3d11','d3d12'), ('x64',), False,
           None, ('feeder','shader','reshade','dlss_runtime','nr_runtime','motion_provider'), ('color','depth','motion_vectors','dlaa_contract'),
           'Feeder transport plus separate consumer Feature 18 success. SR evaluations alone are insufficient.', ('dlss5-feed.log','ReShade.log')),
    Recipe('legacy_bridge', 'Legacy API / 32-bit helper', ('d3d9','d3d11','vulkan','opengl'), ('x86','x64'), False,
           None, ('host64','api_bridge','reshade','motion_provider','dlss_runtime','nr_runtime'), ('shared_color','depth','motion_vectors','host64_transport'),
           'Exact bridge and host contract plus separate NR evaluation in helper. Not generic legacy support.'),
    Recipe('official_swap', 'Official DLSS component swap', ('d3d11','d3d12','vulkan'), ('x64',), True,
           None, (), ('existing_engine_integration',), 'Matching official family/version loaded. Does not add integration or prove NR.'),
    Recipe('configuration', 'Application graphics settings', ('d3d11','d3d12','vulkan'), ('x64','x86'), True,
           None, (), ('existing_engine_settings',), 'Application-confirmed setting. Preset/DLAA quality changes are independent of NR.'),
)
BY_ID = {r.id:r for r in RECIPES}
