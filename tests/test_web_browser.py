"""Opt-in real browser acceptance; remote operations use the scheduler's simulated world."""

import asyncio
import json
import os
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn
import yaml
from test_scheduler import FakeRemote, World

from autobench.web import create_app

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.getenv("AUTOBENCH_BROWSER_TESTS") != "1",
        reason="set AUTOBENCH_BROWSER_TESTS=1 for Chromium acceptance",
    ),
]


@pytest.fixture
def browser_page():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch(
            executable_path=os.getenv("AUTOBENCH_CHROMIUM_EXECUTABLE")
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("dialog", lambda dialog: dialog.accept())
        yield page
        browser.close()
        assert not errors, errors


@pytest.fixture
def ui_server(tmp_path, config, monkeypatch):
    config.machines[0].id = "server01"
    world = World(config)
    original_status = FakeRemote.status
    original_download = FakeRemote.download

    async def slow_status(self, job_id, directory):
        if world.jobs.get(job_id, {}).get("phase") == "active":
            await asyncio.sleep(0.02)
            return {"phase": "active", "state": {"status": "running"}}
        return await original_status(self, job_id, directory)

    async def download(self, directory, inventory, destination):
        await original_download(self, directory, inventory, destination)
        (destination / "run.stdout").write_text("fixture benchmark finished\n")
        (destination / "report.html").write_text("<html>fixture report</html>")

    monkeypatch.setattr(FakeRemote, "status", slow_status)
    monkeypatch.setattr(FakeRemote, "download", download)
    monkeypatch.setattr("autobench.scheduler.RemoteClient", world.factory)
    monkeypatch.setattr(
        "autobench.services.SSHClient",
        lambda ssh, policy: world.factory(config.machines[0], None, policy),
    )
    existing = tmp_path / "existing.yaml"
    existing.write_text(
        "# keep original comment\n" + yaml.safe_dump(config.model_dump(mode="json"))
    )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(base_dir=tmp_path, state_dir=config.policy.state_dir),
            log_level="error",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    yield SimpleNamespace(
        url=f"http://127.0.0.1:{sock.getsockname()[1]}",
        root=tmp_path,
        config=config,
        existing=existing,
        world=world,
    )
    server.should_exit = True
    thread.join(5)
    sock.close()
    assert not thread.is_alive()


def ready(page, server):
    page.goto(server.url)
    page.wait_for_function("() => document.querySelector('#input-machines-0-ssh-host') !== null")


def load_existing(page, server):
    page.locator("#file-menu summary").click()
    page.locator("#open-config").click()
    page.locator("#file-path").fill(str(server.root))
    page.locator("#file-go").click()
    page.get_by_role("button", name="▤ existing.yaml 文件").click()
    page.locator("#file-confirm").click()
    page.wait_for_function("() => document.querySelector('#save-state').textContent === '已保存'")


def fill_connection(page, server):
    page.locator("#input-machines-0-ssh-host").fill("localhost")
    page.locator("#input-machines-0-ssh-username").fill("bench")
    page.locator("#input-machines-0-ssh-password").fill("browser-secret")
    page.get_by_text("高级连接设置", exact=True).click()
    page.locator("#input-machines-0-ssh-known_hosts").fill(str(server.root / "known_hosts"))


def test_wizard_password_probe_files_save_run_and_cancel(browser_page, ui_server):
    page, server = browser_page, ui_server
    ready(page, server)
    assert page.locator("input:visible").count() == 4
    host = page.locator("#input-machines-0-ssh-host").bounding_box()
    port = page.locator("#input-machines-0-ssh-port").bounding_box()
    assert host["y"] == port["y"] and port["width"] < 120
    assert page.locator(".field-mode").count() == 0
    page.locator("#primary-action").click()
    page.locator(".error-link").first.click()
    assert page.locator("#wizard-steps").is_visible()
    assert page.locator("details.advanced").first.evaluate("(e) => e.open") is False
    fill_connection(page, server)
    page.get_by_role("button", name="连接并识别当前系统", exact=True).click()
    page.get_by_role("button", name="添加当前系统", exact=True).wait_for()
    page.get_by_role("button", name="添加当前系统", exact=True).click()
    page.locator("#primary-action").click()
    page.locator("#primary-action").click()
    page.locator('[data-field-path=\'["benchmarks",0,"source_archive"]\']').get_by_role(
        "button", name="选择…"
    ).click()
    page.get_by_role("button", name="▤ source.tar.gz 文件").click()
    page.locator("#file-confirm").click()
    page.locator("#primary-action").click()
    # Configure a private test state directory through the same professional controls.
    page.locator("#workbench-switch").click()
    page.get_by_role("button", name="运行设置", exact=True).click()
    page.get_by_text("高级调度设置", exact=True).click()
    page.locator("#input-policy-state_dir").fill(str(server.config.policy.state_dir))
    page.locator("#input-policy-poll_interval").fill("0.02")
    page.locator("#input-policy-settle_seconds").fill("0")
    page.locator("#workbench-switch").click()
    page.locator("#primary-action").click()
    page.wait_for_function(
        "() => document.querySelector('#primary-action').textContent.includes('开始测试')"
    )
    saved = yaml.safe_load((server.root / "autobench.yaml").read_text())
    assert saved["machines"][0]["ssh"]["password"] == "browser-secret"
    assert (
        not saved["machines"][0]["ssh"]["agent"]
        and saved["machines"][0]["ssh"]["client_keys"] == []
    )
    assert saved["machines"][0]["systems"][0]["machine_id"] == "1" * 32
    page.locator("#primary-action").click()
    page.get_by_role("button", name="取消运行", exact=True).wait_for()
    page.reload()
    page.get_by_role("button", name="取消运行", exact=True).click()
    page.wait_for_function(
        "() => document.querySelector('#run-detail').textContent.includes('运行已取消')"
    )
    page.get_by_text("run.stdout", exact=True).wait_for(timeout=20000)
    page.locator(".artifact-row").filter(has_text="run.stdout").get_by_role(
        "button", name="预览"
    ).click()
    page.wait_for_function(
        "() => document.querySelector('#artifact-preview').textContent.includes('fixture benchmark finished')"
    )
    with page.expect_download() as info:
        page.get_by_role("link", name="下载完整归档").click()
    assert info.value.suggested_filename.endswith(".zip")
    assert len(server.world.starts) == 1


def test_auth_switches_are_exclusive_and_show_only_selected_controls(browser_page, ui_server):
    page = browser_page
    ready(page, ui_server)
    page.locator("#input-machines-0-ssh-password").fill("remember-me")
    page.get_by_label("认证方式", exact=True).select_option("key")
    assert page.locator("#input-machines-0-ssh-password").count() == 0
    page.locator("#input-machines-0-ssh-client_keys-0").fill("./id_key")
    page.get_by_label("密钥来源", exact=True).select_option("agent")
    assert page.locator("#input-machines-0-ssh-client_keys-0").count() == 0
    page.get_by_label("认证方式", exact=True).select_option("password")
    assert page.locator("#input-machines-0-ssh-password").input_value() == "remember-me"
    page.locator("#mode-yaml").click()
    page.locator("#yaml-panel").wait_for()
    result = yaml.safe_load(page.locator("#yaml-editor").input_value())
    assert result["machines"][0]["ssh"]["password"] == "remember-me"
    assert (
        result["machines"][0]["ssh"]["client_keys"] == []
        and result["machines"][0]["ssh"]["agent"] is False
    )


def test_existing_advanced_values_and_comments_survive_viewing_and_editing(browser_page, ui_server):
    page, server = browser_page, ui_server
    raw = server.config.model_dump(mode="json")
    raw["future_data"] = {"preserve": [None, "", 0]}
    raw["machines"][0]["ssh"].update(
        password="mixed-secret", client_keys=["one", "two"], agent=True
    )
    raw["machines"][0]["systems"][0]["ssh"] = {"password": None, "client_keys": [], "proxy": None}
    raw["machines"][0]["systems"][0]["env"] = {
        "EMPTY": "",
        "NUM": "123",
        "LITERAL": "$PATH $(id) `id`",
    }
    text = "# preserve this comment\n" + yaml.safe_dump(raw)
    server.existing.write_text(text)
    ready(page, server)
    load_existing(page, server)
    assert page.get_by_label("认证方式", exact=True).input_value() == "custom"
    page.get_by_text("高级连接设置", exact=True).click()
    page.locator("#secondary-action").click()
    page.wait_for_function(
        "() => !document.querySelector('#secondary-action').classList.contains('busy')"
    )
    assert server.existing.read_text() == text
    page.locator("#input-machines-0-ssh-host").fill("edited.local")
    page.locator("#secondary-action").click()
    page.wait_for_function("() => document.querySelector('#save-state').textContent === '已保存'")
    expected = json.loads(json.dumps(raw))
    expected["machines"][0]["ssh"]["host"] = "edited.local"
    assert yaml.safe_load(server.existing.read_text()) == expected
    page.locator("#primary-action").click()
    page.locator(".error-link").filter(has_text="future_data").click()
    page.locator("#yaml-panel").wait_for()
    assert yaml.safe_load(page.locator("#yaml-editor").input_value()) == expected


def test_probe_result_becomes_stale_without_mutating_systems(browser_page, ui_server):
    page, server = browser_page, ui_server
    ready(page, server)
    fill_connection(page, server)
    pending = []
    page.route("**/api/operations/*", lambda route: pending.append(route))
    page.get_by_role("button", name="连接并识别当前系统", exact=True).click()
    deadline = time.monotonic() + 5
    while not pending and time.monotonic() < deadline:
        page.wait_for_timeout(20)
    assert pending
    response = page.request.get(pending[0].request.url)
    page.locator("#input-machines-0-ssh-host").fill("different-host")
    pending[0].fulfill(response=response)
    page.get_by_text("连接信息或配置位置已改变，请重新识别。", exact=True).first.wait_for()
    assert page.get_by_role("button", name="添加当前系统", exact=True).count() == 0
    page.locator("#mode-yaml").click()
    page.locator("#yaml-panel").wait_for()
    assert (
        yaml.safe_load(page.locator("#yaml-editor").input_value())["machines"][0]["systems"] == []
    )


def test_saved_response_does_not_clear_newer_edits(browser_page, ui_server):
    page, server = browser_page, ui_server
    ready(page, server)
    load_existing(page, server)
    pending = []
    page.route("**/api/config/save", lambda route: pending.append(route))
    page.locator("#input-machines-0-ssh-username").fill("first-edit")
    page.locator("#secondary-action").click()
    deadline = time.monotonic() + 5
    while not pending and time.monotonic() < deadline:
        page.wait_for_timeout(20)
    assert pending
    response = pending[0].fetch()
    page.locator("#input-machines-0-ssh-username").fill("later-edit")
    pending[0].fulfill(response=response)
    page.wait_for_function(
        "() => !document.querySelector('#secondary-action').classList.contains('busy')"
    )
    assert page.locator("#save-state").text_content() == "未保存"
    assert page.locator("#input-machines-0-ssh-username").input_value() == "later-edit"
    page.unroute("**/api/config/save")
    page.locator("#secondary-action").click()
    page.wait_for_function("() => document.querySelector('#save-state').textContent === '已保存'")
    assert (
        yaml.safe_load(server.existing.read_text())["machines"][0]["ssh"]["username"]
        == "later-edit"
    )


def test_mobile_and_validation_field_navigation(browser_page, ui_server):
    page, server = browser_page, ui_server
    ready(page, server)
    load_existing(page, server)
    page.locator("#input-machines-0-ssh-port").fill("0")
    page.locator("#primary-action").click()
    page.locator("#config-errors").wait_for()
    page.locator(".error-link").first.click()
    assert page.locator("#input-machines-0-ssh-port").get_attribute("aria-invalid") == "true"
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
    page.locator("#mode-yaml").click()
    page.locator("#yaml-panel").wait_for()
    page.locator("#yaml-editor").fill("machines: [\n")
    page.locator("#mode-form").click()
    assert page.locator("#yaml-editor").input_value() == "machines: [\n"
    assert page.locator("#yaml-panel").is_visible()


def test_system_groups_reset_independently(browser_page, ui_server):
    page, server = browser_page, ui_server
    raw = server.config.model_dump(mode="json")
    raw["machines"][0]["systems"][0]["ssh"] = {
        "host": "custom.local",
        "password": None,
        "client_keys": [],
        "proxy": None,
        "host_key_alias": None,
    }
    server.existing.write_text(yaml.safe_dump(raw))
    ready(page, server)
    load_existing(page, server)
    page.locator(".system-nav-item").filter(has_text="os1").click()
    page.get_by_label("单独设置此系统的连接地址或用户名", exact=True).uncheck()
    page.get_by_text("系统高级设置", exact=True).click()
    page.get_by_label("连接代理", exact=True).select_option("inherit")
    page.locator("#mode-yaml").click()
    page.locator("#yaml-panel").wait_for()
    result = yaml.safe_load(page.locator("#yaml-editor").input_value())
    assert result["machines"][0]["systems"][0]["ssh"] == {
        "password": None,
        "client_keys": [],
        "host_key_alias": None,
    }
    assert result["machines"][0]["ssh"] == raw["machines"][0]["ssh"]
    page.locator("#mode-form").click()
    page.locator("#config-body").wait_for()
    page.get_by_label("单独设置此系统的认证方式", exact=True).uncheck()
    page.locator("#mode-yaml").click()
    page.locator("#yaml-panel").wait_for()
    result = yaml.safe_load(page.locator("#yaml-editor").input_value())
    assert result["machines"][0]["systems"][0]["ssh"] == {"host_key_alias": None}


def test_yaml_import_save_as_and_export_preserve_text(browser_page, ui_server):
    page, server = browser_page, ui_server
    ready(page, server)
    text = "# browser-imported\nversion: 1\nmachines: []\nbenchmarks: []\n"
    page.locator("#import").set_input_files(
        {"name": "import.yaml", "mimeType": "text/yaml", "buffer": text.encode()}
    )
    page.get_by_text("已导入 YAML 内容，请确认服务器上的保存位置。", exact=True).wait_for()
    page.locator("#file-menu summary").click()
    page.locator("#save-as").click()
    path = server.root / "import-saved.yaml"
    page.locator("#save-path").fill(str(path))
    page.locator("#path-confirm").click()
    page.wait_for_function("() => document.querySelector('#save-state').textContent === '已保存'")
    assert path.read_text() == text
    page.locator("#file-menu summary").click()
    with page.expect_download() as info:
        page.locator("#export").click()
    assert Path(info.value.path()).read_text() == text


def test_invalid_nested_document_keeps_source_accessible(browser_page, ui_server):
    page = browser_page
    ready(page, ui_server)
    text = "version: 1\nmachines: [null]\nbenchmarks: []\n"
    page.locator("#import").set_input_files(
        {"name": "broken.yaml", "mimeType": "text/yaml", "buffer": text.encode()}
    )
    page.get_by_text("配置结构需要修正", exact=True).wait_for()
    page.locator("#mode-yaml").click()
    page.locator("#yaml-panel").wait_for()
    assert page.locator("#yaml-editor").input_value() == text


def test_pending_cancel_has_explicit_completion_action(browser_page, ui_server):
    from autobench.storage import Store

    page, server = browser_page, ui_server
    store = Store(server.config.policy.state_dir)
    run_id = store.create(server.config)
    store.request_cancel(run_id)
    store.set_run_status(run_id, "paused")
    store.update(store.jobs(run_id)[0]["id"], "cancel_pending")
    store.close()
    page.add_init_script(
        "sessionStorage.setItem('autobench-selection', "
        + json.dumps(json.dumps({"id": run_id, "stateDir": str(server.config.policy.state_dir)}))
        + ")"
    )
    page.goto(server.url)
    page.get_by_role("button", name="继续处理取消", exact=True).click()
    page.wait_for_function(
        "() => document.querySelector('#run-detail').textContent.includes('运行已取消')"
    )
    assert not server.world.starts
