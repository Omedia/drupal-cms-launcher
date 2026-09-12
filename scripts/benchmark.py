#!/usr/bin/env python3
"""Measure real GUI startup, Drupal's browser installer and an installed restart."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import time

ROOT = Path(__file__).resolve().parent.parent
PHASES = {
    "attach_site": ("site.attach.begin", "site.attach.end"),
    "start_php": ("php.start", "runtime.ready"),
    "ui_ready_delivery": ("runtime.ready", "ui.ready"),
}


def run_cli(session, arguments, log, timeout=300):
    result = subprocess.run(["playwright-cli", f"-s={session}", *arguments], capture_output=True, text=True, timeout=timeout)
    log.write_text(result.stdout + result.stderr)
    if result.returncode or "### Error" in result.stdout:
        raise RuntimeError(f"Browser command failed; see {log}")
    return result.stdout


def browser_step(session, url, step, template, directory):
    source = (ROOT / "scripts/benchmark_browser.js").read_text()
    config = {"url": url, "step": step, "template": template}
    script = directory / f"browser-{step}.js"
    script.write_text(source.replace("__BENCHMARK_CONFIG__", json.dumps(config)))
    output = run_cli(session, ["run-code", f"--filename={script}"], directory / f"browser-{step}.log")
    payload = output.split("### Result\n", 1)[1].split("\n### ", 1)[0].strip()
    result = json.loads(payload)
    (directory / f"browser-{step}.json").write_text(json.dumps(result, indent=2))
    return result


class Launcher:
    def __init__(self, app, data, timing_file, php_profile=False):
        self.timing_file = timing_file
        self.timing_file.write_text("")
        self.log = timing_file.with_suffix(".stderr").open("w")
        environment = os.environ.copy()
        environment["DRUPAL_CMS_TIMING_FILE"] = str(timing_file)
        environment["DRUPAL_CMS_NO_BROWSER"] = "1"
        if php_profile:
            environment["DRUPAL_CMS_PHP_TIMING_FILE"] = str(timing_file.with_suffix(".php.ndjson"))
        profile = '(version 1)(allow default)(deny network-outbound (remote ip "*:*"))(allow network-outbound (remote ip "localhost:*"))'
        command = ["sandbox-exec", "-p", profile, str(app / "Contents/MacOS/drupal-cms-launcher"), "--data-dir", str(data)]
        self.started_ns = time.time_ns()
        self.started_monotonic = time.perf_counter()
        self.process = subprocess.Popen(command, env=environment, stdout=self.log, stderr=self.log)
        try:
            self.ready = self.wait_stage("ui.ready", 180)
        except BaseException:
            if self.process.poll() is None:
                self.process.terminate()
            self.process.wait(timeout=10)
            self.log.close()
            raise
        self.observed_seconds = time.perf_counter() - self.started_monotonic

    def events(self):
        result = []
        for line in self.timing_file.read_text().splitlines():
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                pass  # A final append can still be in progress.
        return result

    def wait_stage(self, stage, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in self.events():
                if event["stage"] == "runtime.error":
                    raise RuntimeError(f"Launcher failed; inspect {self.timing_file.parent}")
                if event["stage"] == stage:
                    return event
            if stage != "runtime.stopped" and self.process.poll() is not None:
                raise RuntimeError(f"GUI exited before {stage}; see {self.log.name}")
            time.sleep(0.02)
        raise TimeoutError(f"Timed out waiting for {stage}; see {self.timing_file}")

    def measures(self):
        events = {event["stage"]: event for event in self.events()}
        values = {
            "launch_to_ui_ready": (self.ready["unix_ns"] - self.started_ns) / 1e9,
            "launch_to_first_render": (events["ui.first_render"]["unix_ns"] - self.started_ns) / 1e9,
            "launch_to_backend_ready": (events["runtime.ready"]["unix_ns"] - self.started_ns) / 1e9,
        }
        for name, (begin, end) in PHASES.items():
            values[name] = (events[end]["unix_ns"] - events[begin]["unix_ns"]) / 1e9
        return values

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
        self.process.wait(timeout=10)
        self.wait_stage("runtime.stopped", 60)
        self.log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True, help="App built with the opt-in timing markers")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--template", default="Byte")
    parser.add_argument("--php-profile", action="store_true", help="Add request CPU/cache probes to the disposable site's entry points")
    parser.add_argument("--output", type=Path, default=ROOT / "build/benchmarks" / datetime.now().strftime("run-%Y%m%d-%H%M%S"))
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    app = args.app.resolve()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    os.chmod(args.output, 0o700)
    binary = app / "Contents/MacOS/drupal-cms-launcher"
    metadata = {
        "created_utc": datetime.now(timezone.utc).isoformat(), "app": str(app),
        "binary_sha256": hashlib.file_digest(binary.open("rb"), "sha256").hexdigest(),
        "versions": json.loads((app / "Contents/Resources/manifest.json").read_text()),
        "os": subprocess.check_output(["sw_vers", "-productVersion"], text=True).strip(),
        "cpu": subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], text=True).strip(),
        "logical_cpus": int(subprocess.check_output(["sysctl", "-n", "hw.logicalcpu"], text=True)),
        "memory_bytes": int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True)),
        "template": args.template, "runs": [],
        "php_profile": args.php_profile,
        "method": "Fresh data and browser session per run; real GPUI process with opt-in timing and automatic browser opening suppressed; PHP internet access blocked; no OS cache flush; manual thinking/typing and browser-process startup excluded.",
    }
    for number in range(1, args.runs + 1):
        directory = args.output / f"sample-{number}"
        directory.mkdir(mode=0o700)
        data = directory / "data"
        session = f"cms-bench-{os.getpid()}-{number}"
        launcher = None
        print(f"Sample {number}/{args.runs}: fresh start", flush=True)
        try:
            run_cli(session, ["open", "about:blank"], directory / "browser-open.log")
            launcher = Launcher(app, data, directory / "startup.ndjson", args.php_profile)
            startup = launcher.measures()
            if args.php_profile:
                probe = str(ROOT / "scripts/benchmark_php.php").replace("\\", "\\\\").replace("'", "\\'")
                for relative in ("web/index.php", "web/core/install.php"):
                    entry = data / "site" / relative
                    source = entry.read_text()
                    entry.write_text(source.replace("<?php", "<?php\nrequire '" + probe + "';\n", 1))
                batch = data / "site/web/core/includes/batch.inc"
                source = batch.read_text()
                original = "$callable_resolver = \\Drupal::service(CallableResolver::class);\n      $callable = $callable_resolver->getCallableFromDefinition($callback);\n      $callable(...array_merge($args, [&$batch_context]));"
                if source.count(original) != 1:
                    raise RuntimeError("Review the batch probe against the current Drupal version")
                batch.write_text(source.replace(original, "\\cms_benchmark_operation($callback, array_merge($args, [&$batch_context]));", 1))
            preparation = browser_step(session, launcher.ready["url"], "prepare", args.template, directory)
            print(f"Sample {number}: launcher {startup['launch_to_ui_ready']:.2f}s; first installer page {preparation['measures']['first_installer_page']:.2f}s; installing…", flush=True)
            installation = browser_step(session, launcher.ready["url"], "install", args.template, directory)
            launcher.stop()
            launcher = None
            launcher = Launcher(app, data, directory / "restart.ndjson", args.php_profile)
            restart = launcher.measures()
            reopened = browser_step(session, launcher.ready["url"], "reopen", args.template, directory)
            sample = {
                "number": number, "startup": startup,
                "preparation": preparation, "installation": installation,
                "restart": restart, "reopened": reopened,
            }
            sample["clean_start_seconds"] = startup["launch_to_ui_ready"] + preparation["measures"]["first_installer_page"]
            sample["clean_install_seconds"] = preparation["measures"]["save_site_name"] + preparation["measures"]["select_template"] + installation["measures"]["finish_to_dashboard"]
            sample["browser_install_total_seconds"] = preparation["measures"]["first_installer_page"] + sample["clean_install_seconds"]
            sample["first_time_flow_seconds"] = sample["clean_start_seconds"] + sample["clean_install_seconds"]
            sample["installed_reopen_seconds"] = restart["launch_to_ui_ready"] + reopened["measures"]["installed_homepage"]
            metadata["runs"].append(sample)
            (args.output / "results.json").write_text(json.dumps(metadata, indent=2))
            print(f"Sample {number}: clean start {sample['clean_start_seconds']:.2f}s; install {sample['clean_install_seconds']:.2f}s; installed reopen {sample['installed_reopen_seconds']:.2f}s", flush=True)
        finally:
            if launcher is not None:
                launcher.stop()
            run_cli(session, ["close"], directory / "browser-close.log")
    for metric in ("clean_start_seconds", "clean_install_seconds", "installed_reopen_seconds"):
        values = [run[metric] for run in metadata["runs"]]
        print(f"{metric}: median={statistics.median(values):.3f}s range={min(values):.3f}–{max(values):.3f}s", flush=True)
    print(f"Results: {args.output / 'results.json'}", flush=True)


if __name__ == "__main__":
    main()
