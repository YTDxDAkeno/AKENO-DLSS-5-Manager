"""Localized user-facing FiveM finding labels; exact upstream details stay in diagnostic exports."""
from akeno.i18n import t

LABELS = {
    "fivem_runtime_policy": "The recipe names an unknown runtime trust policy.",
    "fivem_community_evidence": "Bandukids FiveM deployment needs separate offline/client evaluation, allowed plugin loading and compatible client protections.",
    "fivem_no_review": "No reviewed FiveM recipe is available; compatibility is unknown and deployment remains disabled.",
    "fivem_server_permission": "Record an identified owned test server and its written permission.",
    "fivem_graphics_permission": "Graphics plugin permission is unknown or denied.",
    "fivem_pure_unknown": "The effective Pure Mode setting is unknown.",
    "fivem_pure_restricted": "Pure Mode 2 prohibits modified client files.",
    "fivem_known_graphics": "Pure Mode 1 requires approval of the exact graphics component hashes.",
    "fivem_backend_permission": "This backend is not included in the server permission.",
    "fivem_script_hook": "This recipe requires explicit Script Hook permission.",
    "fivem_experiment_consent": "Experimental-test consent is missing.",
    "fivem_platform": "Deployment requires detected 64-bit Windows.",
    "fivem_gpu": "A detected NVIDIA RTX GPU is required.",
    "fivem_gpu_identity": "The exact PCI GPU identity has no matching reviewed evidence.",
    "fivem_vram_unknown": "Dedicated VRAM is unknown.",
    "fivem_memory_evidence": "The available VRAM does not match the reviewed test requirements.",
    "fivem_driver_unknown": "The NVIDIA driver version is unknown.",
    "fivem_generation_evidence": "The reviewed GPU generation differs from this GPU.",
    "fivem_driver_evidence": "The current driver is outside this recipe's reviewed matrix.",
    "fivem_neural_evidence": "Signed-runtime neural creation and evaluation evidence is missing.",
    "fivem_runtime_trust": "A verified local NVIDIA runtime must be imported.",
    "fivem_runtime_integrity": "The runtime hash or NVIDIA signature is invalid or unverified.",
    "fivem_existing_runtime": "An existing runtime does not match the reviewed signed copy.",
    "fivem_runtime_pins": "The recipe's runtime hash set is incomplete.",
    "fivem_package_review": "The package does not match the verified recipe.",
    "fivem_review_source": "The recipe lacks a pinned, reviewable source.",
    "fivem_client_revision": "Client or plugin files differ from the reviewed snapshot.",
    "fivem_inspection": "Client inspection is incomplete; inspect the diagnostic errors.",
    "fivem_edition": "The client is Enhanced or its edition is unknown; this integration is for Legacy.",
    "fivem_protection": "Client protection was detected or cannot be inspected; no bypass is available.",
    "fivem_target": "The plugin target is unsafe or unavailable.",
    "fivem_external_hook": "A graphics hook exists outside the approved plugin target.",
    "fivem_unreviewed_plugin": "An existing plugin hash is not covered by the reviewed recipe.",
    "fivem_identity": "The selected application is not the FiveM client bound to this adapter.",
    "fivem_review_backend": "The recipe names a different backend.",
    "fivem_experimental": "FiveM neural rendering is experimental; performance, VRAM fit and ban safety are not guaranteed.",
}


def describe(finding) -> str:
    if finding.code in LABELS:
        return t(LABELS[finding.code])
    from akeno import i18n
    translated = t(finding.message)
    if i18n.language() == "de" and translated == str(finding.message):
        return t("Additional backend prerequisite; analyze or export diagnostics for the exact technical details.") + " [" + finding.code + "]"
    return translated
