# Free of playwright imports so a conftest can read it without the e2e group.
CONTEXT_ARGS = {
    "viewport": {"width": 1280, "height": 800},
    "device_scale_factor": 1,
    "reduced_motion": "reduce",
    "color_scheme": "light",
    "locale": "en-GB",
    "timezone_id": "UTC",
    "service_workers": "block",
}
