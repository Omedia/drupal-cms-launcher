#!/usr/bin/env python3
"""Offline browser-installer startup, progress persistence and safe shutdown."""

import html.parser
import contextlib
import http.cookiejar
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
BINARY = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "target/debug/drupal-cms-launcher"
RESOURCES = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else ROOT / "build/resources"


class Inputs(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}
        self.action = ""

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == "form":
            self.action = attributes.get("action", "")
        if tag == "input" and attributes.get("name") and attributes.get("type") not in {"checkbox", "radio", "submit"}:
            self.values[attributes["name"]] = attributes.get("value", "")


class Site:
    def __init__(self, data):
        command = [str(BINARY), "--supervise", "--resources", str(RESOURCES), "--data-dir", str(data)]
        if shutil.which("sandbox-exec"):
            profile = '(version 1)(allow default)(deny network-outbound (remote ip "*:*"))(allow network-outbound (remote ip "localhost:*"))'
            command = ["sandbox-exec", "-p", profile, *command]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.events = queue.Queue()
        def reader():
            for line in self.process.stdout:
                self.events.put(json.loads(line))
            self.events.put(None)
        threading.Thread(target=reader, daemon=True).start()
        self.browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), urllib.request.ProxyHandler({}))

    def wait(self, phase, timeout=180):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            event = self.events.get(timeout=max(0.1, deadline - time.monotonic()))
            if event is None:
                raise AssertionError("Supervisor exited early: " + self.process.stderr.read())
            if event["phase"] == "error":
                raise AssertionError(event["message"])
            if event["phase"] == phase:
                return event
        raise AssertionError(f"Timed out waiting for {phase}")

    def send(self, command):
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()

    def open(self):
        ready = self.wait("ready")
        self.url = ready["url"]
        self.send("OPEN")
        event = self.wait("open")
        assert event["url"] == self.url
        return self.url

    def stop(self, eof=False):
        if self.process.poll() is not None:
            return
        if eof:
            self.process.stdin.close()
        else:
            self.send("STOP")
        self.wait("stopped", timeout=60)
        assert self.process.wait(timeout=10) == 0


def fields(browser, url):
    with browser.open(url, timeout=30) as response:
        markup = response.read().decode()
    parser = Inputs()
    parser.feed(markup)
    assert "site_name" in parser.values, "Expected the CMS site-name step, not database configuration or a preinstalled site"
    return parser.values, urllib.parse.urljoin(url, parser.action)


@contextlib.contextmanager
def scratch_directory():
    directory = Path(tempfile.mkdtemp(prefix="cms installer smoke ", dir=ROOT / "build"))
    try:
        yield directory
    except BaseException:
        print(f"Failed smoke-test data retained at {directory}", flush=True)
        raise
    else:
        # Drupal makes sites/default read-only during installation.
        for parent, _, _ in os.walk(directory):
            os.chmod(parent, 0o700)
        shutil.rmtree(directory)


def main():
    assert json.loads((RESOURCES / "manifest.json").read_text())["database"] == "sqlite"
    with scratch_directory() as directory:
        data = Path(directory)
        started = time.monotonic()
        first = Site(data)
        try:
            url = first.open()
            values, action = fields(first.browser, url)
            print(f"Offline startup into the CMS installer: {time.monotonic() - started:.1f}s", flush=True)
            for asset in ["wasm_bg.wasm", "lightningcss_node-1.30.1.wasm"]:
                with first.browser.open(url + "/modules/contrib/canvas/ui/dist/assets/" + asset, timeout=30) as response:
                    assert response.headers.get_content_type() == "application/wasm"
                    assert response.read(4) == b"\0asm", "Canvas editor WASM asset was not served"
            assert "admin_password" not in json.loads((data / "state.json").read_text())
            values["site_name"] = "Launcher installer persistence check"
            values["op"] = "Next"
            with first.browser.open(action, urllib.parse.urlencode(values).encode(), timeout=30) as response:
                assert response.status == 200
                assert "Choose a site template" in response.read().decode()
            duplicate = subprocess.run([str(BINARY), "--supervise", "--resources", str(RESOURCES), "--data-dir", str(data)], input="", capture_output=True, text=True, timeout=10)
            assert duplicate.returncode != 0 and "already running" in duplicate.stdout
            uploads = data / "site/web/sites/default/files"
            uploads.mkdir(parents=True, exist_ok=True)
            (uploads / "smoke.php").write_text("<?php echo 'UNSAFE_EXECUTION';")
            (uploads / "outside.svg").symlink_to(data / "state.json")
            for path in ["/sites/default/files/smoke.php", "/sites/default/files/outside.svg", "/.git/config"]:
                try:
                    response = first.browser.open(url + path, timeout=30)
                    body = response.read().decode()
                except urllib.error.HTTPError as error:
                    body = error.read().decode()
                assert "UNSAFE_EXECUTION" not in body and '"hash_salt"' not in body
            first.stop()
            print("Site-name and bundled-template steps, duplicate-instance protection, router and shutdown checks: passed", flush=True)
            second = Site(data)
            try:
                url = second.open()
                with second.browser.open(url, timeout=30) as response:
                    body = response.read().decode()
                if "Choose a site template" not in body:
                    assert fields(second.browser, url)[0]["site_name"] == values["site_name"]
                second.stop(eof=True)
                print("Installer progress persists across restart; parent-disconnect shutdown: passed", flush=True)
            finally:
                if second.process.poll() is None:
                    second.process.stdin.close()
                    second.process.wait(timeout=60)
        finally:
            if first.process.poll() is None:
                first.process.stdin.close()
                first.process.wait(timeout=60)


if __name__ == "__main__":
    main()
