"""Provision and verify *external*, CI-only FFmpeg; never bundle it in LumenEdit.

Windows uses the FFmpeg-project-linked Gyan vendor's versioned essentials ZIP.
macOS uses Homebrew/core ffmpeg@7, with the top-level version, revision and bottle
digest pinned below. Homebrew's transitive dependency versions are intentionally
not locked; their resolved metadata and installed versions are recorded.

Run before pytest: python scripts/provision_ffmpeg.py --install
Without --install, verify the configured LUMEN_FFMPEG/LUMEN_FFPROBE or PATH tools.
Only a successful capability AND real encode/decode check exports GitHub paths.
"""
from __future__ import annotations

import argparse
from array import array
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
import wave


WINDOWS_VERSION = "9.0.2"
WINDOWS_URL = ("https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/"
               "ffmpeg-9.0.2-essentials_build.zip")
WINDOWS_SHA256 = "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba"
# Published at https://www.gyan.dev/ffmpeg/builds/packages/
# ffmpeg-9.0.2-essentials_build.zip.sha256; checked 2026-10-15.
BREW_FORMULA = "ffmpeg@7"
BREW_VERSION = "7.1.5"
BREW_REVISION = 3
BREW_BOTTLES = {
    "arm64_sonoma": "173d2c9d8793a78a0e19a6135fd8e3ca2cc13f7c9d915506a7f30373df6d86ae",
    "arm64_sequoia": "406fff85c8de693aebb92cbcce3830d538dc912e63a65dd39e9c023f49823dc1",
    "arm64_tahoe": "d749053ce58efb23f6aecf53cc923523561c3a32a1fb2e5ccd690b4b7fb8f240",
    "arm64_golden_gate": "e8ce99d17ed208844f5315c6aaa44c3b32917fb2669a86af74d03bc72d49748c",
    "sonoma": "4c4b49b30121f9cfe00ea53f3a503da382095f540077b5cd4e8d43190789124e",
}
# https://formulae.brew.sh/api/formula/ffmpeg@7.json, checked 2026-10-15.
MACOS_RELEASES = {11: "big_sur", 12: "monterey", 13: "ventura", 14: "sonoma",
                  15: "sequoia", 26: "tahoe", 27: "golden_gate"}
REQUIRED_FILTERS = frozenset({"drawtext", "zscale", "gblur", "vignette", "rubberband", "vidstabdetect", "vidstabtransform"})
REQUIRED_ENCODERS = frozenset({"ffv1", "libx264", "aac", "prores_ks"})


class ProvisionError(RuntimeError):
    pass


def run(command, *, cwd=None, env=None, timeout=120):
    """No shell interpolation; retain failure diagnostics, including loader errors."""
    result = subprocess.run([str(value) for value in command], cwd=cwd, env=env,
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=timeout)
    if result.returncode:
        raise ProvisionError(f"Command failed ({result.returncode}): {command!r}\n"
                             f"{result.stdout}\n{result.stderr}")
    return result.stdout


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checked_download(url, destination, expected_sha256):
    """Verify the immutable pin before any archive is opened or executable run."""
    if not url.startswith("https://"):
        raise ProvisionError("Dependency downloads require HTTPS.")
    destination = Path(destination)
    if destination.is_file() and sha256(destination) == expected_sha256:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".download")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "LumenEdit-CI/1"})
        with urllib.request.urlopen(request, timeout=90) as response, partial.open("wb") as out:
            if not response.geturl().startswith("https://"):
                raise ProvisionError("Dependency download redirected away from HTTPS.")
            size = 0
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > 250 * 1024 * 1024:
                    raise ProvisionError("Dependency archive exceeds the 250 MiB limit.")
                out.write(chunk)
        actual = sha256(partial)
        if actual != expected_sha256:
            raise ProvisionError(f"SHA256 mismatch for {url}: expected {expected_sha256}, got {actual}")
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)
    return destination


def extract_windows_tools(archive_path, destination):
    """Extract only the two fixed executables, never archive-controlled paths."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        for name in ("ffmpeg.exe", "ffprobe.exe"):
            candidates = [info for info in archive.infolist()
                          if info.filename.endswith("/bin/" + name) and not info.is_dir()]
            if len(candidates) != 1 or not 0 < candidates[0].file_size < 400 * 1024 * 1024:
                raise ProvisionError(f"Expected exactly one reasonably sized bin/{name} in archive.")
            with archive.open(candidates[0]) as source, (destination / name).open("wb") as out:
                shutil.copyfileobj(source, out)
    return destination / "ffmpeg.exe", destination / "ffprobe.exe"


def install_windows(directory):
    if platform.machine().lower() not in ("amd64", "x86_64"):
        raise ProvisionError("The pinned Windows package requires an x64 runner.")
    archive = checked_download(WINDOWS_URL, Path(directory) / f"ffmpeg-{WINDOWS_VERSION}.zip",
                               WINDOWS_SHA256)
    ffmpeg, ffprobe = extract_windows_tools(archive, Path(directory) / WINDOWS_VERSION / "bin")
    return ffmpeg, ffprobe, {"provider": "Gyan / codexffmpeg", "url": WINDOWS_URL,
                             "archive_sha256": WINDOWS_SHA256, "version": WINDOWS_VERSION,
                             "variant": "essentials", "external_ci_only": True}


def compatible_bottle(formula, macos_major, machine):
    """Never let --force-bottle select a newer, incompatible macOS bottle."""
    if macos_major not in MACOS_RELEASES:
        raise ProvisionError(f"Unreviewed macOS version {macos_major}; update the bottle policy.")
    if machine not in ("arm64", "x86_64"):
        raise ProvisionError(f"Unsupported macOS architecture {machine}.")
    files = formula.get("bottle", {}).get("stable", {}).get("files", {})
    prefix = "arm64_" if machine == "arm64" else ""
    for version in sorted(MACOS_RELEASES, reverse=True):
        tag = prefix + MACOS_RELEASES[version]
        if version <= macos_major and tag in files:
            return tag, files[tag]
    if "all" in files:
        return "all", files["all"]
    raise ProvisionError(f"No compatible bottle for {formula.get('name', '<unknown>')} on "
                         f"macOS {macos_major} {machine}; source-build fallback is disabled. "
                         "Use a supported newer native runner or review updated dependency pins.")


def check_brew_pin(formula, tag, bottle):
    if (formula.get("name") != BREW_FORMULA or formula.get("tap") != "homebrew/core"
            or formula.get("versions", {}).get("stable") != BREW_VERSION
            or formula.get("revision") != BREW_REVISION):
        raise ProvisionError("Homebrew ffmpeg@7 version/revision changed; review the pin before installing.")
    if tag not in BREW_BOTTLES or bottle.get("sha256") != BREW_BOTTLES[tag]:
        raise ProvisionError(f"Homebrew {tag} bottle changed; review the pinned SHA256 before installing.")


def install_macos():
    brew = shutil.which("brew")
    if not brew:
        raise ProvisionError("Homebrew is required on the native macOS CI runner.")
    major = int(platform.mac_ver()[0].split(".")[0])
    machine = platform.machine().lower()
    env = dict(os.environ, HOMEBREW_NO_AUTO_UPDATE="1", HOMEBREW_NO_INSTALL_CLEANUP="1",
               HOMEBREW_NO_ANALYTICS="1", HOMEBREW_NO_ENV_HINTS="1")

    def brew_run(*args, timeout=120):
        return run([brew, *args], env=env, timeout=timeout)

    # Refresh metadata once, then keep it fixed throughout the preflight/install.
    brew_run("update", timeout=600)
    dependencies = brew_run("deps", "--formula", "--union", BREW_FORMULA).split()
    names = [BREW_FORMULA, *dependencies]
    metadata = json.loads(brew_run("info", "--json=v2", "--formula", *names))["formulae"]
    by_name = {item["name"]: item for item in metadata}
    root = by_name[BREW_FORMULA]
    tag, bottle = compatible_bottle(root, major, machine)
    check_brew_pin(root, tag, bottle)
    resolution = []
    for item in metadata:
        if item.get("tap") != "homebrew/core":
            raise ProvisionError(f"Unexpected dependency tap: {item.get('full_name')}")
        dep_tag, dep_bottle = compatible_bottle(item, major, machine)
        resolution.append({"name": item["name"], "version": item["versions"]["stable"],
                           "revision": item["revision"], "bottle_tag": dep_tag,
                           "bottle_sha256": dep_bottle["sha256"],
                           "ruby_source_checksum": item.get("ruby_source_checksum")})
    # Verify the actual top-level archive against our own pin, in addition to
    # Homebrew's integrity checks against its metadata for every dependency.
    brew_run("fetch", "--force-bottle", BREW_FORMULA, timeout=600)
    cached = Path(brew_run("--cache", "--force-bottle", BREW_FORMULA).strip())
    if sha256(cached) != BREW_BOTTLES[tag]:
        raise ProvisionError("Downloaded Homebrew FFmpeg bottle does not match the pinned SHA256.")
    print(f"Installing verified {BREW_FORMULA} {BREW_VERSION}_{BREW_REVISION} ({tag}).", flush=True)
    brew_run("install", "--formula", "--force-bottle", BREW_FORMULA, timeout=900)
    installed = json.loads(brew_run("info", "--json=v2", "--formula", *names))["formulae"]
    root_installed = next(item for item in installed if item["name"] == BREW_FORMULA)
    expected = f"{BREW_VERSION}_{BREW_REVISION}"
    if not any(item.get("version") == expected and item.get("poured_from_bottle")
               for item in root_installed.get("installed", [])):
        raise ProvisionError(f"Expected the installed {expected} FFmpeg bottle; refusing a source build.")
    prefix = Path(brew_run("--prefix", BREW_FORMULA).strip())
    provenance = {"provider": "Homebrew/core", "formula": BREW_FORMULA,
                  "version": BREW_VERSION, "revision": BREW_REVISION,
                  "bottle_tag": tag, "bottle_sha256": BREW_BOTTLES[tag],
                  "formula_source_checksum": root.get("ruby_source_checksum"),
                  "metadata_url": "https://formulae.brew.sh/api/formula/ffmpeg@7.json",
                  "dependencies_locked": False, "dependency_resolution": resolution,
                  "installed": [{"name": item["name"], "installed": item.get("installed", [])}
                                for item in installed], "external_ci_only": True}
    return prefix / "bin/ffmpeg", prefix / "bin/ffprobe", provenance


def listed_names(output, flag_count):
    pattern = re.compile(r"^[ \t]*[A-Z.]{" + str(flag_count) + r"}[ \t]+([a-zA-Z0-9_]+)[ \t]+", re.MULTILINE)
    return set(pattern.findall(output))


def require_capabilities(filters, encoders):
    # FFmpeg 9 removed the command-support column from -filters (show_filters
    # in fftools/opt_common.c); FFmpeg 7 still prints three flag columns.
    filter_names = listed_names(filters, 2) | listed_names(filters, 3)
    missing_filters = sorted(REQUIRED_FILTERS - filter_names)
    missing_encoders = sorted(REQUIRED_ENCODERS - listed_names(encoders, 6))
    if missing_filters or missing_encoders:
        raise ProvisionError(f"FFmpeg lacks required filters {missing_filters} or encoders {missing_encoders}.")


def smoke_font():
    candidates = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/arial.ttf",
                  Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
                  Path("/System/Library/Fonts/Helvetica.ttc"),
                  Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ProvisionError("No local smoke-test font found; expected Arial, Helvetica or DejaVu Sans.")


def exercise_audio_tempo(ffmpeg, cwd):
    """Run compensated chained Rubber Band on a real local tone and inspect its PCM.

    The bootstrap gate uses only the Python standard library. It checks actual
    pitch and exact padded sample budgets, not just an advertised filter name.
    """
    measurements = []
    for speed, chain, expected_samples in (
            (0.25, "0.5", 96000),
            (4.0, "2", 6000)):
        target = Path(cwd) / f"tempo-{speed}.wav"
        stage = ("adelay=512S:all=1,apad=pad_len=12000,"
                 f"rubberband=tempo={chain}:pitch=1:transients=mixed:window=short:channels=together,"
                 "atrim=start_sample=512,asetpts=N/SR/TB")
        filters = (f"{stage},{stage},apad=whole_len={expected_samples},"
                   f"atrim=end_sample={expected_samples}")
        run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
             "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=0.5",
             "-af", filters, "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "1", target], cwd=cwd)
        with wave.open(str(target), "rb") as wav:
            if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getnframes()) != (48000, 1, 2, expected_samples):
                raise ProvisionError(f"Rubber Band {speed}x did not preserve the exact PCM budget.")
            samples = array("h", wav.readframes(expected_samples))
        if sys.byteorder != "little":
            samples.byteswap()
        # Central continuous-tone window avoids startup, flush silence and WAV
        # framing. The period between zero crossings is more precise than a
        # whole-window integer crossing count on the short 4x output.
        body = samples[expected_samples // 5:expected_samples * 3 // 5]
        crossings = [index for index in range(1, len(body))
                     if body[index - 1] <= 0 < body[index]]
        if len(crossings) < 3 or max(map(abs, body), default=0) < 100:
            raise ProvisionError(f"Rubber Band {speed}x produced no measurable tone.")
        frequency = (len(crossings) - 1) * 48000 / (crossings[-1] - crossings[0])
        if abs(frequency - 440) > 5:
            raise ProvisionError(f"Rubber Band {speed}x changed pitch: {frequency:.3f} Hz.")
        measurements.append({"speed": speed, "sample_count": expected_samples,
                             "sample_rate": 48000, "measured_hz": round(frequency, 3)})
    return measurements


def exercise_tools(ffmpeg, ffprobe):
    """Exercise all required filters/encoders and ffprobe, without network media."""
    with tempfile.TemporaryDirectory(prefix="lumen-ffmpeg-check-") as directory:
        cwd = Path(directory)
        source_font = smoke_font()
        font = "ci-font" + source_font.suffix
        shutil.copyfile(source_font, cwd / font)
        graph = (f"drawtext=fontfile={font}:text=LumenCI:fontsize=12:x=2:y=2,"
                 "zscale=w=64:h=48,gblur=sigma=0.5,vignette=angle=0.2,format=yuv420p")
        run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
             "-f", "lavfi", "-i", "testsrc2=s=64x48:r=4:d=0.5",
             "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=0.5",
             "-map", "0:v:0", "-map", "1:a:0", "-vf", graph, "-c:v", "libx264",
             "-preset", "ultrafast", "-threads", "1", "-c:a", "aac", "-t", "0.5", "smoke.mp4"], cwd=cwd)
        probe = [ffprobe, "-v", "error", "-count_frames", "-show_streams", "-of", "json"]
        streams = json.loads(run([*probe, "smoke.mp4"], cwd=cwd))["streams"]
        video = next((s for s in streams if s.get("codec_type") == "video"), {})
        audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
        if (video.get("codec_name"), video.get("width"), video.get("height"),
                video.get("nb_read_frames"), audio.get("codec_name")) != ("h264", 64, 48, "2", "aac"):
            raise ProvisionError(f"FFmpeg H.264/AAC smoke probe failed: {streams}")
        run([ffmpeg, "-v", "error", "-nostdin", "-y", "-i", "smoke.mp4", "-map", "0:v:0",
             "-an", "-c:v", "ffv1", "-threads", "1", "smoke.mkv"], cwd=cwd)
        lossless = json.loads(run([*probe, "smoke.mkv"], cwd=cwd))["streams"]
        if len(lossless) != 1 or lossless[0].get("codec_name") != "ffv1" or lossless[0].get("nb_read_frames") != "2":
            raise ProvisionError(f"FFV1 encode/decode smoke probe failed: {lossless}")
        # Codec availability only; retained source precision is independently
        # tested with synthetic RGB16 ramps in test_color_highbit_reference.py.
        run([ffmpeg, "-v", "error", "-nostdin", "-y", "-i", "smoke.mp4", "-map", "0:v:0",
             "-an", "-vf", "format=yuv422p10le,setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
             "-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le", "-threads", "1",
             "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "smoke.mov"], cwd=cwd)
        prores = json.loads(run([*probe, "smoke.mov"], cwd=cwd))["streams"]
        if (len(prores) != 1 or prores[0].get("codec_name") != "prores"
                or prores[0].get("profile") != "HQ" or prores[0].get("pix_fmt") != "yuv422p10le"
                or prores[0].get("nb_read_frames") != "2"
                or any(prores[0].get(k) != "bt709" for k in ("color_space", "color_transfer", "color_primaries"))):
            raise ProvisionError(f"ProRes 10-bit encode/decode smoke probe failed: {prores}")
        # The app-level gate exercises all bounded factors, actual pitch,
        # compensated landmarks and a selected final transient. CI installs
        # the project before this external-dependency gate.
        from lumen.audio_timing import ensure_retime_audio_capability
        try:
            audio_compatibility = ensure_retime_audio_capability(str(ffmpeg), 30)
        except ValueError as exc:
            raise ProvisionError(str(exc)) from exc
        return {"h264_aac_frames": 2, "ffv1_frames": 2, "prores_hq_frames": 2, "width": 64, "height": 48,
                "filters_exercised": sorted(REQUIRED_FILTERS), "font": str(source_font),
                "tempo_audio": exercise_audio_tempo(ffmpeg, cwd),
                "audio_compatibility": audio_compatibility}


def verify_tools(ffmpeg, ffprobe, expected_version=None):
    ffmpeg, ffprobe = Path(ffmpeg).resolve(strict=True), Path(ffprobe).resolve(strict=True)
    versions = {"ffmpeg": run([ffmpeg, "-version"]), "ffprobe": run([ffprobe, "-version"])}
    if expected_version:
        for name, output in versions.items():
            if not re.match(rf"^{name} version n?{re.escape(expected_version)}(?:[-\s]|$)", output):
                raise ProvisionError(f"Expected {name} {expected_version}, got {output.splitlines()[:1]}")
    require_capabilities(run([ffmpeg, "-hide_banner", "-filters"]),
                         run([ffmpeg, "-hide_banner", "-encoders"]))
    smoke = exercise_tools(ffmpeg, ffprobe)
    return {"ffmpeg": str(ffmpeg), "ffprobe": str(ffprobe), "versions": versions,
            "ffmpeg_sha256": sha256(ffmpeg), "ffprobe_sha256": sha256(ffprobe),
            "required_filters": sorted(REQUIRED_FILTERS), "required_encoders": sorted(REQUIRED_ENCODERS),
            "smoke": smoke}


def export_github_paths(ffmpeg, ffprobe):
    paths = {"LUMEN_FFMPEG": str(ffmpeg), "LUMEN_FFPROBE": str(ffprobe)}
    if any("\n" in value or "\r" in value for value in paths.values()):
        raise ProvisionError("Executable paths must not contain newlines.")
    if os.environ.get("GITHUB_ENV"):
        with Path(os.environ["GITHUB_ENV"]).open("a", encoding="utf-8") as out:
            out.writelines(f"{key}={value}\n" for key, value in paths.items())
    if os.environ.get("GITHUB_PATH"):
        with Path(os.environ["GITHUB_PATH"]).open("a", encoding="utf-8") as out:
            for directory in dict.fromkeys(str(Path(value).parent) for value in paths.values()):
                out.write(directory + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--install-dir", type=Path,
                        default=Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir())) / "lumen-external-ffmpeg")
    parser.add_argument("--report", type=Path, default=Path("build/ffmpeg-provenance.json"))
    parser.add_argument("--ffmpeg", default=os.environ.get("LUMEN_FFMPEG") or shutil.which("ffmpeg"))
    parser.add_argument("--ffprobe", default=os.environ.get("LUMEN_FFPROBE") or shutil.which("ffprobe"))
    args = parser.parse_args(argv)
    report = {"status": "failed", "platform": sys.platform, "machine": platform.machine()}
    try:
        if args.install:
            if sys.platform == "win32":
                ffmpeg, ffprobe, provider = install_windows(args.install_dir)
            elif sys.platform == "darwin":
                ffmpeg, ffprobe, provider = install_macos()
            else:
                raise ProvisionError("Automatic CI provisioning supports Windows and macOS only.")
        else:
            ffmpeg, ffprobe, provider = args.ffmpeg, args.ffprobe, {"provider": "existing external tools"}
        report["provisioning"] = provider
        if not ffmpeg or not ffprobe:
            raise ProvisionError("Both external ffmpeg and ffprobe are required.")
        report["verification"] = verify_tools(ffmpeg, ffprobe, provider.get("version"))
        export_github_paths(report["verification"]["ffmpeg"], report["verification"]["ffprobe"])
        report["status"] = "passed"
        print("FFmpeg capability and real H.264/AAC/FFV1/ProRes HQ encode/decode checks passed.")
    except (ProvisionError, OSError, ValueError, KeyError, subprocess.TimeoutExpired,
            zipfile.BadZipFile) as error:
        report["error"] = str(error)
        print(f"FFmpeg provisioning failed: {error}", file=sys.stderr)
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
