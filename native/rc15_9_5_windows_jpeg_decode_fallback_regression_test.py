import importlib.util
import tempfile
from pathlib import Path
from unittest import mock

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("agent", HERE/"uyap_archive_agent.py")
agent=importlib.util.module_from_spec(spec); spec.loader.exec_module(agent)

def req(cond,msg):
    if not cond: raise AssertionError(msg)

def standard_jpeg():
    return (b"\xff\xd8" +
            b"\xff\xc0\x00\x11\x08\x00\x10\x00\x10\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00" +
            b"\xff\xda\x00\x0c\x03\x01\x00\x02\x00\x03\x00\x00\x3f\x00" +
            b"abc" + b"\xff\xd9")

ping=agent.dispatch({"action":"ping","probe":"rc1595"})
req(ping.get("version")=="2.7.64", ping)
req("jpeg_windows_decoder_fallback_v1" in ping.get("capabilities",[]), ping)

with tempfile.TemporaryDirectory() as td:
    p=Path(td)/"sample.jpg"; p.write_bytes(standard_jpeg())
    prof=agent.classify_image_payload(p)
    req(prof.get("valid") is True and prof.get("mode")=="jpeg_marker_structure", prof)

    with mock.patch.object(agent, "_jpeg_dimensions", return_value=None), \
         mock.patch.object(agent, "_jpeg_compatible_profile", return_value={"valid":False,"mode":"jpeg_compatible_marker_sync"}), \
         mock.patch.object(agent, "_windows_gdiplus_jpeg_probe", return_value={"valid":True,"kind":"JPEG","extension":".jpg","mode":"jpeg_windows_gdiplus_decode","width":2047,"height":2047}):
        prof=agent.classify_image_payload(p)
        req(prof.get("valid") is True, prof)
        req(prof.get("mode")=="jpeg_windows_gdiplus_decode", prof)
        req(prof.get("fallbackFromMode")=="jpeg_compatible_marker_sync", prof)
        req(prof.get("width")==2047 and prof.get("height")==2047, prof)

    bad=Path(td)/"trailing.jpg"; bad.write_bytes(standard_jpeg()+b"EVIL_TRAILING_PAYLOAD")
    with mock.patch.object(agent, "_jpeg_dimensions", return_value=None), \
         mock.patch.object(agent, "_jpeg_compatible_profile", return_value={"valid":False,"mode":"jpeg_compatible_trailing_data"}), \
         mock.patch.object(agent, "_windows_gdiplus_jpeg_probe") as probe:
        prof=agent.classify_image_payload(bad)
        req(prof.get("valid") is False, prof)
        req(probe.call_count==0, "decoder fallback must not run for arbitrary trailing data")

print("RC15.9.5 WINDOWS JPEG DECODE FALLBACK REGRESSION: PASS")
