"""Opt-in real browser acceptance; remote operations use the scheduler's simulated world."""

import asyncio
import json
import os
import socket
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest
import uvicorn
import yaml
from conftest import REMOVED_OUTPUTS
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
        engine = os.getenv("AUTOBENCH_BROWSER_ENGINE", "chromium")
        executable = os.getenv("AUTOBENCH_BROWSER_EXECUTABLE")
        if engine == "chromium" and not executable:
            executable = os.getenv("AUTOBENCH_CHROMIUM_EXECUTABLE")
        browser = getattr(runtime, engine).launch(executable_path=executable)
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
        if PurePosixPath(directory).parent.name == "builds":
            (destination / "build.stdout").write_text("fixture build finished\n")
            (destination / "build-environment.json").write_text('{"COMMON":"yes"}')
            return
        (destination / "run.stdout").write_text("fixture benchmark finished\n")
        (destination / "run-environment.json").write_text('{"COMMON":"yes","ROUND":"1"}')
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


def fill_startup_destination(page, path):
    page.locator("#startup-directory").fill(str(path.parent))
    page.locator("#startup-filename").fill(path.name)


def complete_startup(page, path=None):
    if path is not None:
        fill_startup_destination(page, path)
    page.locator("#startup-save").click()
    page.locator("#page-config").wait_for()


def ready(page, server, startup_path=None):
    page.goto(server.url)
    complete_startup(page, startup_path)


def artifact_folder(page, path):
    return page.locator(f'.artifact-folder[data-keep="folder-{path}"]')


def open_artifact_folder(page, path):
    parts = PurePosixPath(path).parts
    for depth in range(1, len(parts) + 1):
        folder = artifact_folder(page, "/".join(parts[:depth]))
        folder.wait_for()
        if folder.get_attribute("open") is None:
            folder.locator(":scope > summary").click()


@pytest.mark.parametrize("width", [1440, 390])
def test_startup_blocks_workbench_until_new_config_is_saved(browser_page, ui_server, width):
    page, server = browser_page, ui_server
    page.set_viewport_size({"width": width, "height": 750})
    page.goto(server.url)
    dialog = page.get_by_role("dialog", name="选择配置文件")
    directory = page.get_by_label("保存目录", exact=True)
    filename = page.get_by_label("配置文件名", exact=True)
    folder = server.root / "new configs"
    folder.mkdir()
    target = folder / "new-benchmark.yaml"
    page.wait_for_function(
        "path => document.activeElement.id === 'startup-directory' && "
        "document.activeElement.value === path",
        arg=str(server.root),
    )
    assert filename.input_value() == "autobench.yaml"
    box = dialog.bounding_box()
    assert box["x"] + box["width"] / 2 == pytest.approx(width / 2, abs=1)
    assert box["y"] + box["height"] / 2 == pytest.approx(750 / 2, abs=1)
    for _ in range(5):
        page.keyboard.press("Tab")
        assert not page.evaluate("document.activeElement.closest('#app-shell') !== null")
    page.keyboard.press("Escape")
    page.mouse.click(4, 4)
    assert dialog.is_visible()
    assert page.locator("#page-config").is_hidden()
    assert page.locator("#page-runs").is_hidden()
    assert not target.exists()

    page.reload()
    page.get_by_role("button", name="选择目录…", exact=True).click()
    page.get_by_role("dialog", name="选择配置保存目录").wait_for()
    page.get_by_role("button", name="new configs 目录", exact=True).click()
    page.locator("#file-confirm").click()
    assert directory.input_value() == str(folder)
    assert filename.input_value() == "autobench.yaml"
    filename.fill(target.name)
    pending = []
    page.route("**/api/config/save", lambda route: pending.append(route))
    with page.expect_request("**/api/config/save") as request:
        page.locator("#startup-save").click()
    assert request.value.post_data_json["path"] == str(target)
    assert request.value.post_data_json["revision"] is None
    assert page.locator("#startup-save").is_disabled()
    assert page.locator("#startup-browse").is_disabled()
    assert directory.is_disabled()
    assert filename.is_disabled()
    page.locator("#startup-form").evaluate("form => form.requestSubmit()")
    assert len(pending) == 1
    assert dialog.is_visible()
    assert page.locator("#page-config").is_hidden()
    assert not target.exists()
    pending[0].continue_()
    assert_current_page(page, "config")
    assert not page.locator("#startup-dialog").evaluate("el => el.open")
    dialog.wait_for(state="hidden")
    assert page.locator("#save-state").text_content() == "已保存"
    assert page.locator(".wizard-step[aria-current=step]").text_content() == "1连接机器"
    assert yaml.safe_load(target.read_text())["machines"][0]["id"] == "server01"


def test_startup_saves_existing_text_in_selected_directory(browser_page, ui_server):
    page, server = browser_page, ui_server
    original = server.existing.read_bytes().replace(b"\n", b"\r\n")
    server.existing.write_bytes(original)
    page.goto(server.url)
    page.locator("#startup-browse").click()
    page.keyboard.press("Escape")
    page.locator("#file-dialog").wait_for(state="hidden")
    assert page.locator("#startup-dialog").is_visible()
    page.locator("#startup-browse").click()
    assert page.get_by_role("button", name="existing.yaml 文件", exact=True).is_disabled()
    page.locator("#file-confirm").click()
    assert page.locator("#startup-directory").input_value() == str(server.root)
    page.locator("#startup-filename").fill(server.existing.name)
    with page.expect_request("**/api/config/save") as request:
        complete_startup(page)
    assert request.value.post_data_json["text"].encode() == original
    assert request.value.post_data_json["revision"]
    assert server.existing.read_bytes() == original
    assert page.locator("#file-location").text_content() == str(server.existing)


def test_startup_opens_existing_yaml_from_file_picker(browser_page, ui_server):
    page, server = browser_page, ui_server
    original = server.existing.read_bytes()
    page.goto(server.url)
    page.locator("#startup-open").click()
    page.get_by_role("button", name="existing.yaml 文件", exact=True).click()
    with page.expect_request("**/api/config/save") as save:
        page.locator("#file-confirm").click()
    assert save.value.post_data_json["path"] == str(server.existing)
    assert save.value.post_data_json["revision"]
    assert_current_page(page, "config")
    assert page.locator("#file-location").text_content() == str(server.existing)
    assert server.existing.read_bytes() == original


def test_startup_save_errors_keep_config_locked_and_allow_retry(browser_page, ui_server):
    page, server = browser_page, ui_server
    broken = server.root / "broken.yaml"
    broken.write_text("machines: [\n")
    page.goto(server.url)
    directory = page.locator("#startup-directory")
    filename = page.locator("#startup-filename")
    confirm = page.locator("#startup-save")
    alert = page.locator("#startup-error")

    filename.fill("missing/config.yaml")
    confirm.click()
    alert.wait_for()
    assert "路径填写在保存目录中" in alert.inner_text()
    fill_startup_destination(page, broken)
    confirm.click()
    alert.wait_for()
    assert "YAML 格式错误" in alert.inner_text()
    assert broken.read_text() == "machines: [\n"
    fill_startup_destination(page, server.root / "missing" / "config.yaml")
    confirm.click()
    alert.wait_for()
    assert directory.input_value() == str(server.root / "missing")
    assert filename.input_value() == "config.yaml"
    assert page.locator("#page-config").is_hidden()

    changed = server.existing.read_bytes() + b"# external change\n"

    def change_before_save(route):
        server.existing.write_bytes(changed)
        route.continue_()

    page.route("**/api/config/save", change_before_save)
    fill_startup_destination(page, server.existing)
    confirm.click()
    alert.wait_for()
    assert "外部修改" in alert.inner_text()
    assert server.existing.read_bytes() == changed
    assert page.locator("#startup-dialog").is_visible()
    assert page.locator("#page-config").is_hidden()
    page.unroute("**/api/config/save")
    complete_startup(page)
    assert server.existing.read_bytes() == changed


def test_startup_requires_confirmation_in_new_tab_with_copied_session(browser_page, ui_server):
    page, server = browser_page, ui_server
    ready(page, server, startup_path=server.existing)
    page.locator("#tab-runs").click()
    with page.expect_popup() as popup_event:
        page.evaluate("window.open(location.href)")
    popup = popup_event.value
    try:
        popup.locator("#startup-dialog").wait_for()
        assert popup.evaluate("sessionStorage.getItem('autobench-page')") == "runs"
        popup.wait_for_function(
            "name => document.querySelector('#startup-filename').value === name",
            arg=server.existing.name,
        )
        assert popup.locator("#startup-directory").input_value() == str(server.root)
        assert popup.locator("#page-config").is_hidden()
        assert popup.locator("#page-runs").is_hidden()
        complete_startup(popup)
        assert_current_page(popup, "config")
        assert_current_page(page, "runs")
    finally:
        popup.close()


def load_existing(page, server):
    page.locator("#file-menu summary").click()
    page.locator("#open-config").click()
    page.wait_for_function(
        "() => document.querySelector('#file-list').getAttribute('aria-busy') === 'false'"
    )
    page.locator("#file-path").fill(str(server.root))
    with page.expect_response("**/api/files?*"):
        page.locator("#file-go").click()
    page.wait_for_function(
        "() => document.querySelector('#file-list').getAttribute('aria-busy') === 'false'"
    )
    page.get_by_role("button", name="existing.yaml 文件", exact=True).click()
    page.locator("#file-confirm").click()
    page.wait_for_function("() => document.querySelector('#save-state').textContent === '已保存'")


@pytest.mark.parametrize("editing", ["selected", "typed-and-blurred"])
def test_initial_directory_response_preserves_path_editing(browser_page, ui_server, editing):
    page, server = browser_page, ui_server
    ready(page, server)
    destination = server.root / "configs"
    destination.mkdir()
    (destination / "existing.yaml").write_text(server.existing.read_text())
    pending = []
    page.route("**/api/files?*", lambda route: pending.append(route))
    page.locator("#file-menu summary").click()
    with page.expect_request("**/api/files?*"):
        page.locator("#open-config").click()
    path = page.locator("#file-path")
    if editing == "selected":
        # A fill selects the input before inserting text in a separate browser call.
        path.focus()
        path.evaluate("el => el.select()")
    else:
        path.fill(str(destination))
        path.press("Tab")
    pending[0].continue_()
    page.wait_for_function(
        "() => document.querySelector('#file-list').getAttribute('aria-busy') === 'false'"
    )
    if editing == "selected":
        page.keyboard.insert_text(str(destination))
    assert path.input_value() == str(destination)
    with page.expect_request("**/api/files?*"):
        page.locator("#file-go").click()
    pending[1].continue_()
    page.wait_for_function(
        "path => document.querySelector('#file-path').value === path && "
        "document.querySelector('#file-list').getAttribute('aria-busy') === 'false'",
        arg=str(destination),
    )
    page.get_by_role("button", name="existing.yaml 文件", exact=True).click()
    page.locator("#file-confirm").click()
    page.wait_for_function("() => document.querySelector('#save-state').textContent === '已保存'")


@pytest.mark.parametrize("stale_error", [False, True])
def test_file_navigation_blocks_old_entries_and_ignores_stale_responses(
    browser_page, ui_server, stale_error
):
    page, server = browser_page, ui_server
    ready(page, server)
    pending = []
    page.route("**/api/files?*", lambda route: pending.append(route))
    page.locator("#file-menu summary").click()
    with page.expect_request("**/api/files?*"):
        page.locator("#open-config").click()
    pending[0].continue_()
    entry = page.get_by_role("button", name="existing.yaml 文件", exact=True)
    entry.wait_for()
    entry.click()
    assert page.locator("#file-confirm").is_enabled()

    with page.expect_request("**/api/files?*"):
        page.locator("#file-go").click()
    assert page.locator("#file-list").get_attribute("aria-busy") == "true"
    assert page.locator("#file-confirm").is_disabled()
    assert page.locator(".file-entry").count() == 0
    page.locator("#file-hidden").check()
    assert page.locator(".file-entry").count() == 0

    # A newer navigation completes before the previous directory response.
    with page.expect_request("**/api/files?*"):
        page.locator("#file-go").click()
    pending[2].continue_()
    entry.click()
    assert page.locator("#file-selected").text_content() == str(server.existing)
    with page.expect_response("**/api/files?*") as response:
        if stale_error:
            pending[1].fulfill(status=404, json={"detail": "outdated directory"})
        else:
            pending[1].continue_()
    response.value.finished()
    page.evaluate(
        "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
    )
    assert len(pending) == 3
    assert page.locator("#file-selected").text_content() == str(server.existing)
    assert page.locator("#file-list").get_attribute("aria-busy") == "false"
    assert page.locator("#file-confirm").is_enabled()
    page.locator("#file-confirm").click()
    page.wait_for_function("() => document.querySelector('#save-state').textContent === '已保存'")


def reveal_file_menu(page):
    menu = page.locator("#file-menu")
    if not menu.evaluate("e => e.open"):
        menu.locator("summary").click()


def set_editor_mode(page, mode, *, succeeds=True):
    toggle = page.locator("#mode-toggle")
    checked = str(mode == "yaml").lower()
    if toggle.get_attribute("aria-checked") != checked:
        reveal_file_menu(page)
        toggle.click()
    if not succeeds:
        page.wait_for_function("() => !document.querySelector('#mode-toggle').hasAttribute('aria-busy')")
        assert toggle.get_attribute("aria-checked") != checked
        assert page.locator('#notice[data-tone="error"]').is_visible()
        return
    page.wait_for_function(
        "checked => document.querySelector('#mode-toggle').getAttribute('aria-checked') === checked",
        arg=checked,
    )


def save_draft(page):
    reveal_file_menu(page)
    page.locator("#secondary-action").click()


def reveal_steps(page):
    picker = page.locator("#step-picker")
    if picker.is_visible() and picker.get_attribute("aria-expanded") == "false":
        picker.click()


def click_primary(page):
    reveal_steps(page)
    page.locator("#primary-action").click()


def open_step(page, name):
    reveal_steps(page)
    page.locator("#wizard-steps").get_by_role("button", name=name).click()


def open_system(page, name):
    open_step(page, "选择系统")
    page.locator(".systems-board").get_by_role("button", name=name, exact=True).click()


def fill_connection(page, server):
    page.locator("#input-machines-0-ssh-host").fill("localhost")
    page.locator("#input-machines-0-ssh-username").fill("bench")
    page.locator("#input-machines-0-ssh-password").fill("browser-secret")
    page.locator("#input-machines-0-ssh-known_hosts").fill(str(server.root / "known_hosts"))


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("tone,duration", [("success", 3000), ("warning", 5000), ("error", 5000)])
@pytest.mark.parametrize("reduced_motion", ["no-preference", "reduce"])
def test_notices_auto_dismiss(browser_page, ui_server, width, tone, duration, reduced_motion):
    page = browser_page
    page.set_viewport_size({"width": width, "height": 900})
    page.emulate_media(reduced_motion=reduced_motion)
    ready(page, ui_server)
    page.clock.install(time=0)
    page.clock.pause_at(1000)
    notice = page.locator("#notice")
    assert notice.is_hidden()
    original_height = page.locator("main").evaluate("el => el.getBoundingClientRect().height")
    original_position = page.locator(".project-bar").bounding_box()
    page.evaluate(
        """async tone => {
            const { show } = await import('/static/state.js');
            show('操作提示', tone);
        }""",
        tone,
    )
    assert notice.is_visible()
    assert notice.text_content() == "操作提示"
    assert notice.get_attribute("data-tone") == tone
    assert notice.get_attribute("role") == "status"
    assert notice.get_attribute("aria-live") == "polite"
    assert notice.evaluate("el => getComputedStyle(el).position") == "fixed"
    assert page.locator(".project-bar").bounding_box() == original_position
    assert (
        page.locator("main").evaluate("el => el.getBoundingClientRect().height") == original_height
    )
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.clock.run_for(duration - 1)
    assert notice.is_visible()
    page.clock.run_for(1)
    if reduced_motion == "no-preference":
        assert notice.get_attribute("data-phase") == "closing"
        assert notice.text_content() == "操作提示"
        assert notice.evaluate("el => el.inert")
        page.clock.run_for(120)
    assert notice.is_hidden()
    assert notice.text_content() == ""
    assert notice.bounding_box() is None
    assert page.locator(".project-bar").bounding_box() == original_position
    assert (
        page.locator("main").evaluate("el => el.getBoundingClientRect().height") == original_height
    )


@pytest.mark.parametrize(
    "first_tone,second_tone,second_message,duration",
    [
        ("success", "success", "第一条提示", 3000),
        ("success", "success", "第二条提示", 3000),
        ("success", "error", "第二条提示", 5000),
        ("error", "success", "第二条提示", 3000),
    ],
)
@pytest.mark.parametrize("during_exit", [False, True])
def test_new_notice_restarts_dismiss_timer(
    browser_page, ui_server, first_tone, second_tone, second_message, duration, during_exit
):
    page = browser_page
    ready(page, ui_server)
    page.clock.install(time=0)
    page.clock.pause_at(1000)
    show = """async ({message, tone}) => {
        const { show } = await import('/static/state.js');
        show(message, tone);
    }"""
    page.evaluate(show, {"message": "第一条提示", "tone": first_tone})
    first_duration = 5000 if first_tone == "error" else 3000
    page.clock.run_for(first_duration if during_exit else 2500)
    if during_exit:
        assert page.locator("#notice").get_attribute("data-phase") == "closing"
    page.evaluate(show, {"message": second_message, "tone": second_tone})
    page.clock.run_for(duration - 1)
    notice = page.locator("#notice")
    assert notice.is_visible()
    assert notice.text_content() == second_message
    assert notice.get_attribute("data-tone") == second_tone
    page.clock.run_for(1)
    assert notice.get_attribute("data-phase") == "closing"
    page.clock.run_for(120)
    assert notice.is_hidden()
    assert notice.text_content() == ""


@pytest.mark.parametrize("width", [1440, 390])
def test_notice_enter_and_exit_animations(browser_page, ui_server, width):
    page = browser_page
    page.set_viewport_size({"width": width, "height": 900})
    page.emulate_media(reduced_motion="no-preference")
    ready(page, ui_server)
    page.clock.install(time=0)
    page.clock.pause_at(1000)
    entrance = page.evaluate(
        """async () => {
            const { show } = await import('/static/state.js');
            show('动画提示');
            const notice = document.querySelector('#notice');
            const animation = notice.getAnimations()[0];
            animation.pause();
            animation.currentTime = 100;
            const result = {
                name: animation.animationName,
                duration: animation.effect.getTiming().duration,
                opacity: Number(getComputedStyle(notice).opacity),
                translate: getComputedStyle(notice).translate,
            };
            animation.finish();
            return result;
        }"""
    )
    assert entrance["name"] == "popover-enter"
    assert entrance["duration"] == 180
    assert 0 < entrance["opacity"] < 1
    assert entrance["translate"] != "none"
    page.clock.run_for(3000)
    exit_state = page.locator("#notice").evaluate(
        """notice => {
            const animation = notice.getAnimations()[0];
            animation.pause();
            animation.currentTime = 70;
            return {
                name: animation.animationName,
                duration: animation.effect.getTiming().duration,
                opacity: Number(getComputedStyle(notice).opacity),
            };
        }"""
    )
    assert exit_state["name"] == "notice-exit"
    assert exit_state["duration"] == 120
    assert 0 < exit_state["opacity"] < 1
    page.clock.run_for(120)
    assert page.locator("#notice").is_hidden()


@pytest.mark.parametrize("width", [1440, 390])
def test_long_notice_stays_in_viewport_when_scrolling(browser_page, ui_server, width):
    page = browser_page
    page.set_viewport_size({"width": width, "height": 600})
    page.emulate_media(reduced_motion="reduce")
    ready(page, ui_server)
    page.clock.install(time=0)
    page.clock.pause_at(1000)
    page.evaluate(
        """async () => {
            const { show } = await import('/static/state.js');
            show('LongErrorDetail'.repeat(200), 'error');
        }"""
    )
    notice = page.locator("#notice")
    before = notice.bounding_box()
    assert before["x"] >= 16
    assert before["x"] + before["width"] <= width - 16
    assert before["y"] >= page.locator(".topbar").bounding_box()["height"]
    assert before["y"] + before["height"] < 600
    assert notice.evaluate("el => el.scrollHeight > el.clientHeight")
    page.evaluate("window.scrollTo(0, 400)")
    assert page.evaluate("window.scrollY") > 0
    assert notice.bounding_box() == before
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.parametrize("width", [1440, 390])
def test_save_draft_notice_dismisses_and_keeps_saved_state(browser_page, ui_server, width):
    page, server = browser_page, ui_server
    page.set_viewport_size({"width": width, "height": 900})
    ready(page, server)
    load_existing(page, server)
    page.clock.install(time=0)
    page.clock.pause_at(1000)
    save_draft(page)
    page.get_by_text("草稿已保存，可稍后继续完善。", exact=True).wait_for()
    assert page.locator("#notice").is_visible()
    page.clock.run_for(2999)
    assert page.locator("#notice").is_visible()
    page.clock.run_for(1)
    assert page.locator("#notice").get_attribute("data-phase") == "closing"
    page.clock.run_for(120)
    assert page.locator("#notice").is_hidden()
    assert page.locator("#save-state").text_content() == "已保存"
    assert server.existing.is_file()


def assert_current_page(page, key):
    page.wait_for_function(
        "key => sessionStorage.getItem('autobench-page') === key",
        arg=key,
    )
    titles = {"config": "测试配置", "runs": "运行记录"}
    page.locator(f"#page-{key}").wait_for()
    assert page.locator("[data-page].active").count() == 1
    assert page.locator("[data-page].active").get_attribute("data-page") == key
    selected = page.get_by_role("tab", selected=True)
    assert selected.count() == 1
    assert selected.inner_text() == titles[key]
    assert selected.get_attribute("tabindex") == "0"
    assert selected.get_attribute("aria-controls") == f"page-{key}"
    assert page.locator(f"#page-{key}").get_attribute("aria-labelledby") == selected.get_attribute(
        "id"
    )
    assert page.get_by_role("tabpanel").count() == 1
    assert (
        page.get_by_role("tabpanel").get_by_role("heading", name=titles[key], level=1, exact=True).count()
        == 1
    )
    assert page.get_by_role("tab", selected=False).get_attribute("tabindex") == "-1"
    for other in titles.keys() - {key}:
        assert page.locator(f"#page-{other}").is_hidden()
        assert page.locator(f"#header-{other}").is_hidden()


@pytest.mark.parametrize("key", ["config", "runs"])
def test_reload_does_not_flash_config_while_script_or_api_is_loading(browser_page, ui_server, key):
    page = browser_page
    ready(page, ui_server)
    page.locator(f'[data-page="{key}"]').click()
    scripts, visible_while_loading = [], []

    def check_loading(route):
        visible_while_loading.extend(
            selector
            for selector in ["#startup-dialog", "#page-config", "#page-runs"]
            if page.locator(selector).is_visible()
        )
        route.continue_()

    page.route("**/static/app.js", lambda route: scripts.append(route))
    page.route("**/api/info", check_loading)
    page.reload(wait_until="commit")
    page.locator(".page-tabs").wait_for()
    assert page.locator("#page-config").is_hidden()
    assert page.locator("#header-config").is_hidden()
    assert page.locator("#header-runs").is_hidden()
    assert page.locator("[data-page].active").count() == 0
    assert page.get_by_role("tab", selected=True).count() == 0
    assert scripts
    with page.expect_response("**/api/info"):
        scripts[0].continue_()
    assert not visible_while_loading
    assert_current_page(page, key)
    assert page.locator("#startup-dialog").is_hidden()


@pytest.mark.parametrize("key", ["config", "runs"])
def test_tab_page_survives_reload_without_selected_run(browser_page, ui_server, key):
    page = browser_page
    ready(page, ui_server)
    page.locator(f'[data-page="{key}"]').click()
    page.reload()
    assert_current_page(page, key)
    if key == "runs":
        page.get_by_text("此目录暂无运行记录", exact=True).wait_for()
        assert page.locator("#state-location").text_content() == str(
            ui_server.config.policy.state_dir
        )
    assert page.evaluate("sessionStorage.getItem('autobench-selection')") is None


def test_config_editor_view_survives_reload(browser_page, ui_server):
    page, server = browser_page, ui_server
    ready(page, server, startup_path=server.existing)
    open_system(page, "编辑系统配置 os2")
    system_id = page.get_by_label("系统标识 *", exact=True)
    page.reload()
    system_id.wait_for()
    assert system_id.input_value() == "os2"

    set_editor_mode(page, "yaml")
    page.reload()
    page.locator("#yaml-editor").wait_for()
    assert page.locator("#yaml-editor").input_value() == server.existing.read_text()
    set_editor_mode(page, "form")
    assert system_id.input_value() == "os2"

    load_existing(page, server)
    page.locator("#input-machines-0-ssh-host").wait_for()
    assert page.locator(".wizard-step[aria-current=step]").text_content() == "1连接机器"


def test_reload_restores_only_reachable_config_step(browser_page, ui_server):
    page, server = browser_page, ui_server
    ready(page, server, startup_path=server.existing)
    open_step(page, "检查运行")
    document = yaml.safe_load(server.existing.read_text())
    document["machines"][0]["systems"] = []
    server.existing.write_text(yaml.safe_dump(document))

    page.reload()
    page.get_by_role("heading", name="尚未添加系统", exact=True).wait_for()
    assert page.locator(".wizard-step[aria-current=step]").text_content() == "2选择系统"
    assert page.locator("#wizard-step-4").is_disabled()


def test_run_detail_returns_to_filtered_list(browser_page, ui_server):
    from autobench.storage import Store

    page, server = browser_page, ui_server
    store = Store(server.config.policy.state_dir)
    run_id = store.create(server.config)
    store.set_run_status(run_id, "failed")
    store.close()
    ready(page, server)
    page.locator("#tab-runs").click()
    page.locator("#run-search").fill(run_id)
    page.locator("#run-filter").select_option("failed")
    row_action = page.get_by_role("button", name="查看详情", exact=True)
    row_action.click()
    assert page.locator("#runs-overview").is_hidden()
    assert page.evaluate("document.activeElement.id") == "back-to-runs"
    page.locator("#run-detail").get_by_text(run_id, exact=True).wait_for()
    page.locator("#back-to-runs").click()
    assert page.locator("#run-search").input_value() == run_id
    assert page.locator("#run-filter").input_value() == "failed"
    assert row_action.evaluate("e => e === document.activeElement")
    page.locator("#run-search").fill("no-match")
    page.get_by_role("button", name="清除筛选", exact=True).click()
    assert page.locator("#run-filter").input_value() == ""
    assert row_action.is_visible()


@pytest.mark.parametrize("key", ["config", "runs"])
def test_selected_run_does_not_override_page_after_reload(browser_page, ui_server, key):
    from autobench.storage import Store

    page, server = browser_page, ui_server
    history = server.root / "other-history"
    store = Store(history)
    run_id = store.create(server.config)
    store.close()
    ready(page, server)
    page.locator('[data-page="runs"]').click()
    page.locator("#change-location").click()
    page.wait_for_function(
        "() => document.querySelector('#file-selected').textContent !== ''",
    )
    page.locator("#file-path").fill(str(history))
    page.locator("#file-go").click()
    page.wait_for_function(
        "path => document.querySelector('#file-selected').textContent === path",
        arg=str(history),
    )
    page.locator("#file-confirm").click()
    page.get_by_role("button", name="查看详情", exact=True).click()
    page.locator("#run-detail").get_by_text(run_id, exact=True).wait_for()
    page.locator(f'[data-page="{key}"]').click()
    for _ in range(2):
        page.reload()
        page.wait_for_function(
            "id => document.querySelector('#run-detail').textContent.includes(id)",
            arg=run_id,
        )
        assert_current_page(page, key)
    page.locator('[data-page="runs"]').click()
    page.locator("#run-detail").get_by_text(run_id, exact=True).wait_for()
    assert page.locator("#runs-overview").is_hidden()
    page.locator("#back-to-runs").click()
    assert page.locator("#state-location").text_content() == str(history)
    assert page.get_by_role("button", name="查看详情", exact=True).count() == 1
    page.reload()
    assert_current_page(page, "runs")
    assert page.locator("#state-location").text_content() == str(history)
    assert page.locator("#run-detail").is_hidden()


@pytest.mark.parametrize("saved_page", [None, "unknown", "__proto__"])
def test_missing_or_invalid_page_defaults_to_config_with_saved_run(
    browser_page, ui_server, saved_page
):
    from autobench.storage import Store

    page, server = browser_page, ui_server
    store = Store(server.config.policy.state_dir)
    run_id = store.create(server.config)
    store.close()
    ready(page, server)
    page.evaluate(
        """({savedPage, selection}) => {
            sessionStorage.setItem('autobench-selection', JSON.stringify(selection));
            if (savedPage === null) sessionStorage.removeItem('autobench-page');
            else sessionStorage.setItem('autobench-page', savedPage);
        }""",
        {
            "savedPage": saved_page,
            "selection": {"id": run_id, "stateDir": str(server.config.policy.state_dir)},
        },
    )
    page.reload()
    page.wait_for_function(
        "id => document.querySelector('#run-detail').textContent.includes(id)",
        arg=run_id,
    )
    assert_current_page(page, "config")
    page.locator('[data-page="runs"]').click()
    page.locator("#run-detail").get_by_text(run_id, exact=True).wait_for()


def assert_log_preview_fills_drawer(drawer):
    bottom_gap = drawer.evaluate(
        """element => {
            const log = element.querySelector('#preview-content');
            const style = getComputedStyle(element);
            return element.getBoundingClientRect().bottom - log.getBoundingClientRect().bottom
                - parseFloat(style.paddingBottom) - parseFloat(style.borderBottomWidth);
        }"""
    )
    assert bottom_gap == pytest.approx(0, abs=1)


@pytest.mark.parametrize(("width", "text_size"), [(1440, 100), (390, 100), (320, 100), (1440, 200)])
def test_readable_run_names_and_nested_downloads(
    browser_page, ui_server, monkeypatch, width, text_size
):
    from playwright.sync_api import expect

    from autobench.storage import Store

    page, server = browser_page, ui_server
    name = "2026-11-03_14-30-25"
    with monkeypatch.context() as patch:
        patch.setattr("autobench.storage.time.strftime", lambda *args: name)
        store = Store(server.config.policy.state_dir)
        store.create(server.config)
        run_id = store.create(server.config)
        store.close()
    filename = "localhost.localdomain-2026-11-05-01.log"
    relative = f"server01/os1/ub/round-001/results/{filename}"
    result = server.config.policy.output_dir / run_id / relative
    result.parent.mkdir(parents=True)
    result.write_text("readable archive result\n" * 300)
    page.set_viewport_size({"width": width, "height": 844})
    page.emulate_media(reduced_motion="no-preference")
    if text_size == 200:

        def apply_text_zoom(route):
            response = route.fetch()
            route.fulfill(response=response, body=response.text() + "\nhtml { font-size: 200%; }")

        page.route("**/static/style.css", apply_text_zoom)
    ready(page, server)
    page.locator('[data-page="runs"]').click()
    page.locator("#runs-list").get_by_text(name, exact=True).wait_for()
    row = page.locator("#runs-list tr").filter(has_text=run_id)
    row.get_by_text(run_id, exact=True).wait_for()
    row.get_by_role("button", name="查看详情", exact=True).click()
    page.locator("#run-detail").get_by_text(run_id, exact=True).wait_for()
    open_artifact_folder(page, PurePosixPath(relative).parent)
    artifact = page.locator(".artifact-row").filter(has_text=filename)
    preview = artifact.get_by_role("button", name="预览", exact=True)
    drawer = page.get_by_role("dialog", name=filename)
    close = drawer.get_by_role("button", name="关闭预览", exact=True)
    preview.scroll_into_view_if_needed()
    tree = page.locator("#run-detail .artifact-panel")
    tree_position = tree.bounding_box()
    preview.click()
    path = drawer.get_by_role("group", name="文件路径")
    path.get_by_text(relative, exact=True).wait_for()
    log = drawer.locator("#artifact-preview")
    assert log.text_content() == result.read_text()
    assert tree.bounding_box() == tree_position
    assert drawer.locator(".artifact-panel").count() == 0
    drawer_position = drawer.bounding_box()
    close_position = close.bounding_box()
    path_position = path.bounding_box()
    content = drawer.get_by_role("region", name="预览内容")
    content_position = content.bounding_box()
    assert drawer_position["y"] == pytest.approx(page.locator(".topbar").bounding_box()["height"])
    assert drawer_position["x"] > 0
    assert drawer_position["x"] + drawer_position["width"] <= width
    assert_log_preview_fills_drawer(drawer)
    assert drawer.evaluate("element => element.scrollWidth === element.clientWidth")
    assert content.evaluate("element => element.scrollWidth === element.clientWidth")
    assert (
        log.evaluate("element => getComputedStyle(element).fontSize")
        == f"{13 * text_size / 100:g}px"
    )
    if output := os.getenv("AUTOBENCH_UI_SCREENSHOTS"):
        Path(output).mkdir(parents=True, exist_ok=True)
        page.screenshot(
            path=Path(output) / f"log-preview-{width}-{text_size}.png", animations="disabled"
        )
    inside = (content_position["x"] + 30, content_position["y"] + 30)
    outside = (drawer_position["x"] / 2, 200)
    page.mouse.click(*inside)
    assert drawer.is_visible()
    page.mouse.wheel(0, 3000)
    page.wait_for_function("() => document.querySelector('#preview-content').scrollTop > 0")
    assert close.bounding_box() == close_position
    assert path.bounding_box() == path_position
    page.mouse.down()
    page.mouse.move(*outside, steps=4)
    page.mouse.up()
    assert drawer.is_visible()
    page.mouse.click(*outside)
    drawer.wait_for(state="hidden")
    assert preview.evaluate("button => document.activeElement === button")
    preview.click()
    assert drawer.locator("#preview-content").evaluate("element => element.scrollTop") == 0
    expect(drawer).to_have_css("opacity", "1")
    exit_frame = close.evaluate(
        """button => {
            const drawer = button.closest('dialog');
            const bounds = () => Array.from(drawer.children, child => {
                const { x, y, width, height } = child.getBoundingClientRect();
                return [x, y, width, height];
            });
            const before = bounds();
            button.click();
            const fade = drawer.getAnimations().find(animation =>
                animation.transitionProperty === 'opacity'
            );
            if (fade) fade.currentTime = fade.effect.getTiming().duration / 2;
            return {
                before,
                during: bounds(),
                opacity: Number(getComputedStyle(drawer).opacity),
                supportsExit: CSS.supports('interactivity: inert') &&
                    CSS.supports('overlay: auto') && CSS.supports('transition-behavior: allow-discrete'),
            };
        }"""
    )
    if exit_frame["supportsExit"]:
        assert 0 < exit_frame["opacity"] < 1
        for during, before in zip(exit_frame["during"], exit_frame["before"], strict=True):
            assert during == pytest.approx(before, abs=1)
    drawer.wait_for(state="hidden")
    expect(preview).to_be_focused()
    page.emulate_media(reduced_motion="reduce")
    preview.click()
    close.wait_for()
    assert not drawer.evaluate("element => element.getAnimations().length")
    page.keyboard.press("Escape")
    drawer.wait_for(state="hidden")
    with page.expect_download() as downloaded_file:
        artifact.get_by_role("link", name="下载", exact=True).click()
    assert downloaded_file.value.suggested_filename == filename
    assert Path(downloaded_file.value.path()).read_text() == result.read_text()
    with page.expect_download() as downloaded_archive:
        page.get_by_role("link", name="下载完整归档", exact=True).click()
    assert downloaded_archive.value.suggested_filename == f"autobench-{run_id}.zip"
    with zipfile.ZipFile(downloaded_archive.value.path()) as zipped:
        assert zipped.read(f"{run_id}/{relative}") == result.read_bytes()


@pytest.mark.parametrize("width", [1440, 1024, 768, 390, 320])
def test_run_artifact_tree_groups_file_paths_and_keeps_expansion(browser_page, ui_server, width):
    from autobench.storage import Store

    page, server = browser_page, ui_server
    store = Store(server.config.policy.state_dir)
    run_id = store.create(server.config)
    store.close()
    root = server.config.policy.output_dir / run_id
    benchmark = "server01/os1/ub"
    first = f"{benchmark}/round-001"
    second = f"{benchmark}/round-002"
    report_name = "localhost.localdomain-" + "report" * 18 + ".html"
    files = {
        "summary.txt": "run summary",
        f"{benchmark}/build.stdout": "shared build log",
        f"{first}/run.stdout": "round one",
        f"{first}/results/{report_name}": "<html></html>",
        f"{second}/run.stdout": "round two",
    }
    for path, value in files.items():
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(value)
    page.set_viewport_size({"width": width, "height": 844})
    page.clock.install()
    ready(page, server)
    page.locator('[data-page="runs"]').click()
    page.get_by_role("button", name="查看详情", exact=True).click()
    root_tree = page.locator(".artifact-tree").first
    root_file = root_tree.locator(":scope > .artifact-row")
    root_filename = root_file.locator("strong").filter(has_text="summary.txt")
    root_filename.wait_for()
    folder_toggle = artifact_folder(page, "server01").locator(":scope > summary")
    toggle_start = folder_toggle.evaluate(
        "element => element.getBoundingClientRect().left + "
        "parseFloat(getComputedStyle(element).paddingInlineStart)"
    )
    assert toggle_start == pytest.approx(root_filename.bounding_box()["x"], abs=1)
    assert toggle_start == pytest.approx(
        root_file.locator(".file-location > summary").bounding_box()["x"], abs=1
    )
    assert artifact_folder(page, benchmark).get_attribute("open") is None
    open_artifact_folder(page, first)
    first_folder = artifact_folder(page, first)
    second_folder = artifact_folder(page, second)
    common = artifact_folder(page, benchmark).locator(":scope > .artifact-tree > .artifact-row")
    assert common.locator("strong").text_content() == "build.stdout"
    first_log = first_folder.get_by_role("button", name="预览", exact=True)
    second_log = second_folder.get_by_role("button", name="预览", exact=True)
    assert first_log.is_visible() and second_log.is_hidden()
    reports = artifact_folder(page, f"{first}/results")
    reports.locator(":scope > summary").click()
    assert reports.get_by_text(report_name, exact=True).is_visible()
    assert reports.get_by_role("link", name="下载", exact=True).is_visible()
    assert reports.get_by_role("button", name="预览", exact=True).count() == 0
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

    (root / first / "run.stderr").write_text("newly collected log")
    page.clock.fast_forward(10000)
    first_folder.get_by_text("run.stderr", exact=True).wait_for()
    assert reports.get_by_text(report_name, exact=True).is_visible()
    assert second_log.is_hidden()

    second_folder.locator(":scope > summary").press("Enter")
    second_log.click()
    drawer = page.get_by_role("dialog", name="run.stdout")
    drawer.wait_for()
    assert drawer.locator("#artifact-preview").text_content() == "round two"
    assert drawer.locator("#preview-path").text_content() == f"{second}/run.stdout"
    assert_log_preview_fills_drawer(drawer)
    page.keyboard.press("Escape")
    drawer.wait_for(state="hidden")
    with page.expect_download() as downloaded_file:
        second_folder.get_by_role("link", name="下载", exact=True).click()
    assert Path(downloaded_file.value.path()).read_text() == "round two"


@pytest.mark.parametrize("remembered", ["inspect", "unknown"])
def test_removed_or_unknown_page_returns_to_config(browser_page, ui_server, remembered):
    page = browser_page
    ready(page, ui_server)
    page.evaluate("value => sessionStorage.setItem('autobench-page', value)", remembered)
    page.reload()
    assert_current_page(page, "config")
    assert page.locator("#page-inspect, [data-page=inspect], .sidebar, #breadcrumb").count() == 0
    assert page.get_by_role("tab").count() == 2


def test_tabs_support_keyboard_navigation(browser_page, ui_server):
    page = browser_page
    ready(page, ui_server)
    config = page.get_by_role("tab", name="测试配置", exact=True)
    runs = page.get_by_role("tab", name="运行记录", exact=True)
    config.focus()
    for key, selected, target in [
        ("ArrowRight", "runs", runs),
        ("ArrowRight", "config", config),
        ("ArrowLeft", "runs", runs),
        ("Home", "config", config),
        ("End", "runs", runs),
    ]:
        page.keyboard.press(key)
        assert_current_page(page, selected)
        assert target.evaluate("element => element === document.activeElement")
    page.keyboard.press("Tab")
    assert page.locator("#page-runs").evaluate("element => element === document.activeElement")
    page.keyboard.press("Tab")
    assert page.locator("#use-config-location").evaluate(
        "element => element === document.activeElement"
    )
    page.keyboard.press("Tab")
    assert page.locator("#change-location").evaluate(
        "element => element === document.activeElement"
    )
    page.keyboard.press("Tab")
    assert page.locator("#run-search").evaluate("element => element === document.activeElement")


@pytest.mark.parametrize("width", [1920, 1440, 1200, 1101, 1100, 768, 700, 640, 390, 320])
def test_header_files_layout_and_independent_locations(browser_page, ui_server, width):
    page, server = browser_page, ui_server
    page.set_viewport_size({"width": width, "height": 900})
    ready(page, server)
    load_existing(page, server)
    long_dir = server.root / ("history-" + "long-path-" * 18)
    long_dir.mkdir()
    saved_path = long_dir / ("configuration-" * 10 + ".yaml")
    page.locator("#file-menu summary").click()
    page.locator("#save-as").click()
    page.locator("#save-path").fill(str(saved_path))
    page.locator("#path-confirm").click()
    page.wait_for_function(
        "path => document.querySelector('#file-location').textContent === path",
        arg=str(saved_path),
    )
    assert saved_path.is_file()
    page.locator("#input-machines-0-ssh-host").fill("unsaved.example")
    open_step(page, "运行设置")
    page.locator("#file-menu summary").click()
    assert page.locator(".menu-items").is_visible()
    page.locator("#tab-runs").click()
    assert_current_page(page, "runs")
    assert not page.locator("#file-menu").evaluate("el => el.open")
    page.locator("#change-location").click()
    page.wait_for_function("() => document.querySelector('#file-selected').textContent !== ''")
    page.locator("#file-path").fill(str(long_dir))
    page.locator("#file-go").click()
    page.wait_for_function(
        "path => document.querySelector('#file-selected').textContent === path",
        arg=str(long_dir),
    )
    page.locator("#file-confirm").click()
    page.wait_for_function(
        "path => document.querySelector('#state-location').textContent === path",
        arg=str(long_dir),
    )
    assert page.locator("#state-location").get_attribute("title") == str(long_dir)
    for key in ("config", "runs"):
        page.locator(f"#tab-{key}").click()
        assert_current_page(page, key)
        page.wait_for_function(
            """() => Math.abs(parseFloat(getComputedStyle(document.documentElement)
                .getPropertyValue('--header-offset')) -
                document.querySelector('.topbar').getBoundingClientRect().height) < 1"""
        )
        header = page.locator(f"#header-{key}").bounding_box()
        top = page.locator(".topbar").bounding_box()
        assert page.locator(".service-info").is_visible() == (width > 850)
        if key == "config":
            menu = page.locator("#file-menu summary").bounding_box()
            assert page.locator(".topbar #header-config").is_visible()
            assert header["y"] >= top["y"]
            assert header["y"] + header["height"] <= top["y"] + top["height"]
            assert menu["x"] + menu["width"] <= top["x"] + top["width"]
            page.locator("#file-menu summary").click()
            assert page.locator("#file-location").is_visible()
            menu_panel = page.locator("#file-menu .menu-items").bounding_box()
            assert menu_panel["x"] >= 0 and menu_panel["x"] + menu_panel["width"] <= width
            assert page.locator("#file-location").inner_text() == str(saved_path)
            page.locator("#change-config-path").click()
            assert page.locator("#save-path").input_value() == str(saved_path)
            page.get_by_role("button", name="取消", exact=True).click()
        else:
            assert header["y"] > top["y"] + top["height"]
            assert header["y"] + header["height"] < page.locator("#runs-overview").bounding_box()["y"]
            count = page.locator("#run-count").bounding_box()
            search = page.locator("#run-search").bounding_box()
            if width <= 600:
                assert count["y"] + count["height"] < search["y"]
            else:
                assert count["y"] + count["height"] / 2 == pytest.approx(
                    search["y"] + search["height"] / 2, abs=1
                )
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate("window.scrollTo(0, 400)")
        assert page.locator(".topbar").bounding_box()["y"] == 0
    page.locator("#tab-config").click()
    assert "运行设置" in page.locator('.wizard-step[aria-current="step"]').text_content()
    open_step(page, "连接机器")
    assert page.locator("#input-machines-0-ssh-host").input_value() == "unsaved.example"
    assert page.locator("#file-location").get_attribute("title") == str(saved_path)
    assert page.locator("#file-name").get_attribute("title") == str(saved_path)
    assert page.locator("#save-state").text_content() == "未保存"
    assert page.locator("#save-state").get_attribute("title") == "未保存"
    page.locator("#tab-runs").click()
    assert page.locator("#state-location").inner_text() == str(long_dir)
    page.locator("#use-config-location").click()
    page.wait_for_function(
        "path => document.querySelector('#state-location').textContent === path",
        arg=str(server.config.policy.state_dir),
    )


@pytest.mark.parametrize("width", [1440, 768, 390])
def test_header_mode_switch_supports_keyboard_and_keeps_source(browser_page, ui_server, width):
    page = browser_page
    page.set_viewport_size({"width": width, "height": 900})
    ready(page, ui_server)
    load_existing(page, ui_server)
    open_step(page, "运行设置")
    header_height = page.locator(".topbar").bounding_box()["height"]
    toggle = page.locator("#mode-toggle")
    reveal_file_menu(page)
    assert page.locator("#header-config #mode-toggle").is_visible()
    assert page.locator(".editor-toolbar, #mode-form, #mode-yaml").count() == 0
    assert toggle.get_attribute("aria-checked") == "false"
    toggle.focus()
    page.keyboard.press("Space")
    page.locator("#yaml-panel").wait_for()
    assert toggle.get_attribute("aria-checked") == "true"
    assert page.locator("#wizard-steps").is_hidden()
    text = page.locator("#yaml-editor").input_value() + "\n# keep mode-switch comment\n"
    page.locator("#yaml-editor").fill(text)
    page.locator("#tab-runs").click()
    assert toggle.is_hidden()
    page.locator("#tab-config").click()
    assert toggle.get_attribute("aria-checked") == "true"
    assert page.locator("#yaml-editor").input_value() == text
    reveal_file_menu(page)
    toggle.focus()
    page.keyboard.press("Enter")
    page.locator("#config-layout").wait_for()
    assert toggle.get_attribute("aria-checked") == "false"
    assert "运行设置" in page.locator('.wizard-step[aria-current="step"]').text_content()
    set_editor_mode(page, "yaml")
    assert page.locator("#yaml-editor").input_value() == text
    assert page.locator(".topbar").bounding_box()["height"] == header_height
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_header_mode_switch_waits_for_parse_before_updating(browser_page, ui_server):
    page = browser_page
    ready(page, ui_server)
    set_editor_mode(page, "yaml")
    pending = []
    page.route("**/api/config/parse", lambda route: pending.append(route))
    toggle = page.locator("#mode-toggle")
    reveal_file_menu(page)
    toggle.click()
    page.wait_for_function(
        "() => document.querySelector('#mode-toggle').getAttribute('aria-busy') === 'true'"
    )
    assert len(pending) == 1
    assert toggle.get_attribute("aria-checked") == "true"
    assert page.locator("#yaml-panel").is_visible()
    page.keyboard.press("Enter")
    page.keyboard.press("Space")
    assert len(pending) == 1
    pending[0].continue_()
    page.wait_for_function(
        "() => document.querySelector('#mode-toggle').getAttribute('aria-checked') === 'false'"
    )
    assert toggle.is_enabled()
    assert page.locator("#config-layout").is_visible()


@pytest.mark.parametrize("width", [1440, 768, 390])
def test_tabs_keep_configuration_draft_and_step(browser_page, ui_server, width):
    page = browser_page
    page.set_viewport_size({"width": width, "height": 900})
    saved_path = ui_server.existing
    ready(page, ui_server, startup_path=saved_path)
    saved_text = saved_path.read_bytes()
    page.locator("#input-machines-0-ssh-host").fill("unsaved.example")
    open_step(page, "运行设置")
    page.get_by_role("tab", name="运行记录", exact=True).click()
    assert_current_page(page, "runs")
    page.get_by_role("tab", name="测试配置", exact=True).click()
    assert page.locator('.wizard-step[aria-current="step"]').count() == 1
    assert "运行设置" in page.locator('.wizard-step[aria-current="step"]').text_content()
    open_step(page, "连接机器")
    assert page.locator("#input-machines-0-ssh-host").input_value() == "unsaved.example"
    set_editor_mode(page, "yaml")
    text = page.locator("#yaml-editor").input_value() + "\n# unsaved tab draft\n"
    page.locator("#yaml-editor").fill(text)
    page.get_by_role("tab", name="运行记录", exact=True).click()
    page.get_by_role("tab", name="测试配置", exact=True).click()
    assert page.locator("#yaml-editor").is_visible()
    assert page.locator("#yaml-editor").input_value() == text
    assert page.locator("#save-state").text_content() == "未保存"
    assert saved_path.read_bytes() == saved_text
    assert page.locator(".brand").is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.parametrize("width", [1440, 390])
def test_incomplete_prerequisites_disable_steps_before_and_after_reload(
    browser_page, ui_server, width
):
    page, server = browser_page, ui_server
    page.set_viewport_size({"width": width, "height": 900})
    ready(page, server)
    errors = page.locator("#config-errors")
    active_step = page.locator(".wizard-step[aria-current=step]")
    steps = page.locator(".wizard-step")
    edit_tests = page.get_by_role("button", name="编辑测试", exact=True)

    reveal_steps(page)
    assert steps.nth(1).is_disabled()
    assert steps.nth(1).evaluate("e => getComputedStyle(e).cursor") == "not-allowed"
    assert steps.nth(1).evaluate("e => getComputedStyle(e).color") != steps.nth(0).evaluate(
        "e => getComputedStyle(e).color"
    )
    assert edit_tests.is_disabled()
    steps.nth(1).evaluate("e => e.click()")
    assert active_step.get_attribute("id") == "wizard-step-0"
    assert errors.is_hidden()

    click_primary(page)
    assert "请填写主机地址。" in errors.inner_text()
    assert errors.locator(".error-link").first.evaluate("e => e === document.activeElement")

    page.locator("#input-machines-0-ssh-host").fill("localhost")
    page.locator("#input-machines-0-ssh-username").fill("bench")
    assert steps.nth(1).is_enabled()
    assert steps.nth(2).is_disabled()
    save_draft(page)
    page.wait_for_function("() => document.querySelector('#save-state').textContent === '已保存'")

    for _ in range(2):
        page.reload()
        page.locator("#input-machines-0-ssh-host").wait_for()
        reveal_steps(page)
        assert steps.nth(1).is_enabled()
        assert all(step.is_disabled() for step in steps.all()[2:])
        target = steps.nth(4).bounding_box()
        page.mouse.click(target["x"] + target["width"] / 2, target["y"] + target["height"] / 2)
        assert active_step.get_attribute("id") == "wizard-step-0"
        assert errors.is_hidden()

    page.evaluate("async () => (await import('/static/config-view.js')).goToStep(4)")
    assert active_step.get_attribute("id") == "wizard-step-0"
    open_step(page, "选择系统")
    assert active_step.get_attribute("id") == "wizard-step-1"
    click_primary(page)
    assert "添加至少一个系统" in errors.inner_text()

    page.get_by_role("button", name="手动添加系统", exact=True).click()
    page.locator("#input-machines-0-systems-0-boot_entry").fill("0001")
    assert steps.nth(2).is_enabled()
    assert steps.nth(3).is_disabled()
    assert edit_tests.is_enabled()
    edit_tests.click()
    assert active_step.get_attribute("id") == "wizard-step-2"
    click_primary(page)
    assert "请选择服务器上的源码包。" in errors.inner_text()

    open_step(page, "连接机器")
    assert active_step.get_attribute("id") == "wizard-step-0"
    open_step(page, "设置测试")
    assert active_step.get_attribute("id") == "wizard-step-2"
    page.locator("#input-benchmarks-0-source_archive").fill(
        str(server.config.benchmarks[0].source_archive)
    )
    open_step(page, "检查运行")
    assert active_step.get_attribute("id") == "wizard-step-4"
    assert errors.is_hidden()
    open_step(page, "连接机器")
    page.locator("#input-machines-0-ssh-host").clear()
    assert steps.nth(4).is_disabled()
    click_primary(page)
    assert active_step.get_attribute("id") == "wizard-step-0"
    assert "请填写主机地址。" in errors.inner_text()


def test_wizard_password_connection_test_manual_system_files_save_run_and_cancel(
    browser_page, ui_server
):
    page, server = browser_page, ui_server
    ready(page, server)
    assert page.locator(".machine-connection h2").all_text_contents() == [
        "连接信息", "身份认证", "主机密钥校验", "代理设置", "远端环境"
    ]
    assert page.locator(".machine-connection .hint").count() == 0
    labels = page.locator(".machine-connection :is(.field-label label, label.field-label)")
    assert labels.count() == 9
    assert labels.evaluate_all(
        """labels => labels.every(label => {
            const style = getComputedStyle(label);
            return style.visibility === 'visible' && style.clipPath === 'none' &&
                label.getBoundingClientRect().bottom <= label.control.getBoundingClientRect().top;
        })"""
    )
    assert page.locator(".machine-connection input:visible").count() == 8
    host = page.locator("#input-machines-0-ssh-host").bounding_box()
    port = page.locator("#input-machines-0-ssh-port").bounding_box()
    assert host["y"] == port["y"] and port["width"] < 120
    username = page.locator("#input-machines-0-ssh-username").bounding_box()
    connect = page.get_by_role("button", name="连接测试", exact=True).bounding_box()
    assert connect["x"] > username["x"] + username["width"]
    assert username["y"] == host["y"] == connect["y"]
    assert username["height"] == host["height"] == connect["height"]
    alias = page.get_by_label("主机别名", exact=True).bounding_box()
    ignore = page.get_by_label("忽略主机密钥检查", exact=True).bounding_box()
    assert alias["x"] == pytest.approx(username["x"], abs=1)
    assert ignore["x"] == pytest.approx(host["x"], abs=1)
    password = page.get_by_label("登录密码 *", exact=True).bounding_box()
    assert ignore["y"] > password["y"] + password["height"]
    known_hosts = page.get_by_label("主机密钥文件", exact=True).bounding_box()
    browse = page.locator(".machine-known-hosts .browse-button").bounding_box()
    assert known_hosts["y"] == alias["y"] == browse["y"]
    assert page.locator(".field-mode").count() == 0
    click_primary(page)
    page.locator(".error-link").first.click()
    assert page.locator("#wizard-steps").is_visible()
    fill_connection(page, server)
    page.get_by_role("button", name="连接测试", exact=True).click()
    page.locator('.connection-test-status[data-phase="success"]').wait_for()
    click_primary(page)
    assert page.get_by_role("button", name="连接测试", exact=True).count() == 0
    assert page.get_by_text("检查连接及运行环境，不提交测试，也不切换操作系统。").count() == 0
    page.get_by_role("button", name="手动添加系统", exact=True).click()
    page.locator("#input-machines-0-systems-0-boot_entry").fill("0001")
    click_primary(page)
    page.locator('[data-field-path=\'["benchmarks",0,"source_archive"]\']').get_by_role(
        "button", name="选择…"
    ).click()
    page.get_by_role("button", name="source.tar.gz 文件", exact=True).click()
    page.locator("#file-confirm").click()
    click_primary(page)
    # The fourth step contains the full scheduling controls.
    assert page.locator("#wizard-steps [aria-current=step]").text_content() == "4运行设置"
    page.get_by_text("高级调度设置", exact=True).click()
    page.locator("#input-policy-state_dir").fill(str(server.config.policy.state_dir))
    page.locator("#input-policy-poll_interval").fill("0.02")
    page.locator("#input-policy-settle_seconds").fill("0")
    click_primary(page)
    click_primary(page)
    page.wait_for_function(
        "() => document.querySelector('#primary-action').textContent.includes('开始测试')"
    )
    saved = yaml.safe_load((server.root / "autobench.yaml").read_text())
    assert saved["machines"][0]["ssh"]["password"] == "browser-secret"
    assert (
        not saved["machines"][0]["ssh"]["agent"]
        and saved["machines"][0]["ssh"]["client_keys"] == []
    )
    assert saved["machines"][0]["systems"][0]["boot_entry"] == "0001"
    click_primary(page)
    page.get_by_role("button", name="取消运行", exact=True).wait_for()
    assert_current_page(page, "runs")
    page.reload()
    assert_current_page(page, "runs")
    page.get_by_role("button", name="取消运行", exact=True).click()
    page.wait_for_function(
        "() => document.querySelector('#run-detail').textContent.includes('运行已取消')"
    )
    machine = saved["machines"][0]
    open_artifact_folder(
        page, f'{machine["id"]}/{machine["systems"][0]["id"]}/{saved["benchmarks"][0]["id"]}/round-001'
    )
    page.locator(".artifact-row").filter(has_text="run.stdout").get_by_role(
        "button", name="预览"
    ).click()
    page.wait_for_function(
        "() => document.querySelector('#artifact-preview').textContent.includes('fixture benchmark finished')"
    )
    page.get_by_role("button", name="关闭预览", exact=True).click()
    with page.expect_download() as info:
        page.get_by_role("link", name="下载完整归档").click()
    assert info.value.suggested_filename.endswith(".zip")
    assert not set(page.locator(".artifact-name strong").all_text_contents()) & REMOVED_OUTPUTS
    with zipfile.ZipFile(info.value.path()) as zipped:
        assert not {PurePosixPath(name).name for name in zipped.namelist()} & REMOVED_OUTPUTS
    assert len(server.world.starts) == 1


def test_auth_switches_are_exclusive_and_show_only_selected_controls(browser_page, ui_server):
    page = browser_page
    ready(page, ui_server)
    help_text = page.locator(".machine-connection .auth-editor :is(.hint, .callout)")
    page.locator("#input-machines-0-ssh-password").fill("remember-me")
    page.get_by_label("认证方式", exact=True).select_option("key")
    assert page.locator("#input-machines-0-ssh-password").count() == 0
    assert help_text.count() == 0
    page.locator("#input-machines-0-ssh-client_keys-0").fill("./id_key")
    page.get_by_label("密钥来源", exact=True).select_option("agent")
    assert page.locator("#input-machines-0-ssh-client_keys-0").count() == 0
    assert help_text.count() == 0
    page.get_by_label("认证方式", exact=True).select_option("password")
    assert page.locator("#input-machines-0-ssh-password").input_value() == "remember-me"
    set_editor_mode(page, "yaml")
    page.locator("#yaml-panel").wait_for()
    result = yaml.safe_load(page.locator("#yaml-editor").input_value())
    assert result["machines"][0]["ssh"]["password"] == "remember-me"
    assert (
        result["machines"][0]["ssh"]["client_keys"] == []
        and result["machines"][0]["ssh"]["agent"] is False
    )


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("width", [1440, 390])
def test_auth_menu_expands_below_select(browser_page, ui_server, fallback, width):
    from playwright.sync_api import expect

    page = browser_page
    page.set_viewport_size({"width": width, "height": 844})
    if fallback:
        page.add_init_script("""const supports = CSS.supports.bind(CSS);
            CSS.supports = (...args) => args[0] !== 'appearance: base-select' && supports(...args);
        """)

        def disable_native_picker(route):
            response = route.fetch()
            route.fulfill(
                response=response,
                body=response.text().replace(
                    "@supports (appearance: base-select)", "@supports (appearance: unsupported)"
                ),
            )

        page.route("**/static/style.css", disable_native_picker)
    ready(page, ui_server)
    auth = page.get_by_role("combobox", name="认证方式", exact=True)
    if fallback:
        menu = page.get_by_role("listbox", name="认证方式", exact=True)
        option = menu.get_by_role("option", name="密钥认证", exact=True)
        selected = menu.get_by_role("option", name="密码认证", exact=True)
    else:
        option = auth.locator('option[value="key"]')
        selected = auth.locator('option[value="password"]')
        expect(auth).to_have_css("align-items", "center")

    expect(auth).to_have_css("border-color", "rgb(118, 137, 166)")
    expect(auth).to_have_css("border-radius", "10px")
    auth.click()
    expect(option).to_be_visible()
    trigger, row = auth.bounding_box(), option.bounding_box()
    assert row["y"] >= trigger["y"] + trigger["height"]
    keyboard_focus = auth.evaluate("el => el.matches(':focus-visible')")
    expect(auth).to_have_css("outline-style", "solid" if keyboard_focus else "none")
    expect(auth).to_have_css(
        "border-color", "rgb(30, 64, 175)" if keyboard_focus else "rgb(77, 131, 220)"
    )
    expect(auth).to_have_css("box-shadow", "rgba(59, 130, 246, 0.1) 0px 0px 0px 3px")
    expect(selected).to_have_css("background-color", "rgb(232, 241, 255)")
    expect(selected).to_have_css("border-radius", "8px")
    assert "data:image/svg+xml" in selected.evaluate("e => getComputedStyle(e).backgroundImage")
    option.hover()
    expect(option).to_have_css("background-color", "rgb(241, 246, 255)")
    option.click()
    expect(auth).to_have_value("key")

    auth.press("Space")
    expect(option).to_be_visible()
    page.keyboard.press("Escape")
    if fallback:
        assert page.locator("#select-menu").evaluate("el => el.inert && el.hidden")
        assert auth.get_attribute("aria-expanded") is None
    else:
        assert not auth.evaluate("el => el.matches(':open')")
    expect(option).to_be_hidden()
    expect(auth).to_be_focused()
    expect(auth).to_have_value("key")

    auth.press("Space")
    expect(option).to_be_visible()
    page.get_by_role("heading", name="身份认证", exact=True).click()
    expect(option).to_be_hidden()
    auth.press("Space")
    page.keyboard.press("ArrowUp")
    page.keyboard.press("Enter")
    expect(auth).to_have_value("password")

    trigger = auth.bounding_box()
    page.set_viewport_size({"width": width, "height": int(trigger["y"] + trigger["height"]) + 36})
    auth.click()
    expect(option).to_be_visible()
    trigger, row = auth.bounding_box(), option.bounding_box()
    assert row["y"] + row["height"] <= trigger["y"]


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
    assert page.locator(".machine-connection .auth-editor :is(.hint, .callout)").count() == 0
    save_draft(page)
    page.wait_for_function(
        "() => !document.querySelector('#secondary-action').classList.contains('busy')"
    )
    assert server.existing.read_text() == text
    page.locator("#input-machines-0-ssh-host").fill("edited.local")
    save_draft(page)
    page.wait_for_function("() => document.querySelector('#save-state').textContent === '已保存'")
    expected = json.loads(json.dumps(raw))
    expected["machines"][0]["ssh"]["host"] = "edited.local"
    assert yaml.safe_load(server.existing.read_text()) == expected
    open_step(page, "检查运行")
    click_primary(page)
    page.locator(".error-link").filter(has_text="future_data").click()
    page.locator("#yaml-panel").wait_for()
    assert yaml.safe_load(page.locator("#yaml-editor").input_value()) == expected


def test_connection_test_spinner_success_and_configuration_invalidation(browser_page, ui_server):
    page, server = browser_page, ui_server
    ready(page, server)
    saved_path = server.root / "autobench.yaml"
    saved_text = saved_path.read_bytes()
    fill_connection(page, server)
    pending = []
    page.route("**/api/operations/*", lambda route: pending.append(route))
    connect = page.get_by_role("button", name="连接测试", exact=True)
    connect.click()
    status = page.locator('.connection-test-status[data-phase="running"]')
    status.wait_for()
    assert connect.is_disabled()
    icon = status.locator(".status-icon")
    assert icon.evaluate("e => getComputedStyle(e).animationName") == "status-spin"
    button_box, icon_box = connect.bounding_box(), icon.bounding_box()
    assert icon_box["x"] >= button_box["x"] + button_box["width"]
    assert button_box["y"] <= icon_box["y"] < button_box["y"] + button_box["height"]
    deadline = time.monotonic() + 5
    while not pending and time.monotonic() < deadline:
        page.wait_for_timeout(20)
    assert len(pending) == 1
    response = page.request.get(pending[0].request.url)
    assert set(response.json()["result"]) == {"checks"}
    pending[0].fulfill(response=response)
    status = page.locator('.connection-test-status[data-phase="success"]')
    status.wait_for()
    assert status.locator(".status-icon svg[aria-hidden=true]").count() == 1
    assert status.evaluate("e => getComputedStyle(e).color") == "rgb(22, 101, 52)"
    assert not connect.is_disabled()
    assert page.locator(".connection-test-panel button").count() == 1
    assert page.locator(".connection-test-panel > *").count() == 1
    card = page.locator(".machine-connection")
    before = card.bounding_box()
    page.locator("#input-machines-0-ssh-host").fill("different-host")
    stale = page.locator('.connection-test-status[data-phase="stale"]')
    stale.wait_for()
    assert stale.get_attribute("aria-label") == "连接信息或配置位置已改变，请重新测试。"
    assert stale.locator(".status-icon").count() == 1
    assert card.bounding_box() == before
    page.locator("#input-machines-0-ssh-host").fill("localhost")
    assert page.locator('.connection-test-status[data-phase="stale"]').count() == 1
    set_editor_mode(page, "yaml")
    page.locator("#yaml-panel").wait_for()
    assert (
        yaml.safe_load(page.locator("#yaml-editor").input_value())["machines"][0]["systems"] == []
    )
    assert saved_path.read_bytes() == saved_text


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize(
    "failure_ids", [["connection"], ["uefi", "runtime", "dependencies", "disk"]]
)
def test_connection_test_failure_summary_and_retry(browser_page, ui_server, width, failure_ids):
    page, server = browser_page, ui_server
    page.set_viewport_size({"width": width, "height": 1000})
    ready(page, server)
    fill_connection(page, server)
    checks = [
        {"id": name, "status": "failed", "message": "具体失败原因：" + "x" * 180}
        for name in failure_ids
    ]
    responses = [
        {"status": "completed", "result": {"checks": checks}},
        {"status": "running", "id": "retry"},
    ]
    page.route("**/api/connections/probe", lambda route: route.fulfill(json=responses.pop(0)))
    pending = []
    page.route("**/api/operations/retry", lambda route: pending.append(route))
    connect = page.get_by_role("button", name="连接测试", exact=True)
    fields = page.locator(".machine-connection-fields")
    connect.click()
    status = page.locator('.connection-test-status[data-phase="error"]')
    status.wait_for()
    assert status.locator(".status-icon svg[aria-hidden=true]").count() == 1
    assert status.evaluate("e => getComputedStyle(e).color") == "rgb(180, 35, 24)"
    labels = {
        "connection": "SSH 连接",
        "uefi": "UEFI 启动项",
        "runtime": "运行环境",
        "dependencies": "基础命令",
        "disk": "可用空间",
    }
    assert status.locator(".status-message").text_content() == "；".join(
        f"{labels[item['id']]}：{item['message']}" for item in checks
    )
    button_box, status_box = connect.bounding_box(), status.bounding_box()
    assert status_box["y"] >= button_box["y"] + button_box["height"]
    assert status_box["width"] >= button_box["width"]
    assert status.locator(".status-icon").bounding_box()["x"] >= status_box["x"] + 8
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    connect.click()
    running = page.locator('.connection-test-status[data-phase="running"]')
    running.wait_for()
    spinner_box = running.bounding_box()
    fields_box = fields.bounding_box()
    assert spinner_box["x"] >= button_box["x"] + button_box["width"] + 8
    assert spinner_box["x"] + spinner_box["width"] <= fields_box["x"] + fields_box["width"]
    assert page.locator(".status-message").count() == 0
    deadline = time.monotonic() + 5
    while not pending and time.monotonic() < deadline:
        page.wait_for_timeout(20)
    assert pending
    pending[0].fulfill(
        json={
            "status": "completed",
            "result": {"checks": [{"id": "connection", "status": "passed", "message": "通过"}]},
        }
    )
    success = page.locator('.connection-test-status[data-phase="success"]')
    success.wait_for()
    assert success.bounding_box() == spinner_box
    assert not connect.is_disabled()


@pytest.mark.parametrize("failure", ["request", "network", "operation", "empty"])
def test_connection_test_errors_are_inline(browser_page, ui_server, failure):
    page, server = browser_page, ui_server
    ready(page, server)
    fill_connection(page, server)

    def respond(route):
        if failure == "request":
            route.fulfill(status=422, json={"detail": "连接参数不合法"})
        elif failure == "network":
            route.abort()
        elif failure == "operation":
            route.fulfill(json={"status": "failed", "error": "连接操作执行失败"})
        else:
            route.fulfill(json={"status": "completed", "result": {"checks": []}})

    page.route("**/api/connections/probe", respond)
    page.get_by_role("button", name="连接测试", exact=True).click()
    status = page.locator('.connection-test-status[data-phase="error"]')
    status.wait_for()
    expected = {
        "request": "连接参数不合法",
        "network": "无法连接 Web 服务，请检查服务是否仍在运行。",
        "operation": "连接操作执行失败",
        "empty": "未获取到检查结果，请重试。",
    }
    assert status.locator(".status-message").text_content() == expected[failure]
    assert not page.get_by_role("button", name="连接测试", exact=True).is_disabled()


@pytest.mark.parametrize("old_failure", [False, True])
def test_connection_test_ignores_outdated_completion(browser_page, ui_server, old_failure):
    page, server = browser_page, ui_server
    ready(page, server)
    fill_connection(page, server)
    pending = []
    page.route("**/api/connections/probe", lambda route: pending.append(route))
    connect = page.get_by_role("button", name="连接测试", exact=True)
    connect.click()
    page.locator('.connection-test-status[data-phase="running"]').wait_for()
    assert len(pending) == 1
    page.locator("#input-machines-0-ssh-host").fill("different-host")
    page.locator('.connection-test-status[data-phase="stale"]').wait_for()
    connect.click()
    page.locator('.connection-test-status[data-phase="running"]').wait_for()
    assert len(pending) == 2
    pending[1].fulfill(
        json={
            "status": "completed",
            "result": {
                "checks": [{"id": "connection", "status": "failed", "message": "新连接失败"}]
            },
        }
    )
    page.locator('.connection-test-status[data-phase="error"]').wait_for()
    older = (
        {"status": "failed", "error": "旧连接失败"}
        if old_failure
        else {
            "status": "completed",
            "result": {"checks": [{"id": "connection", "status": "passed", "message": "通过"}]},
        }
    )
    with page.expect_response("**/api/connections/probe"):
        pending[0].fulfill(json=older)
    page.wait_for_timeout(50)
    assert page.locator(".status-message").text_content() == "SSH 连接：新连接失败"
    assert not connect.is_disabled()


@pytest.mark.parametrize("width", [1440, 390])
def test_warmup_defaults_toggle_save_reload_and_yaml_roundtrip(browser_page, ui_server, width):
    page, server = browser_page, ui_server
    page.set_viewport_size({"width": width, "height": 1000})
    raw = server.config.model_dump(mode="json")
    del raw["benchmarks"][0]["warmup"]
    raw["benchmarks"][0]["repetitions"] = 3
    server.existing.write_text(yaml.safe_dump(raw))
    ready(page, server)
    load_existing(page, server)
    open_step(page, "设置测试")
    control = page.get_by_role("checkbox", name="预热", exact=True)
    assert control.is_checked()
    assert "先完整运行 1 轮，预热结果不保存" in page.locator("#config-body").text_content()
    plan = page.locator("#config-plan")
    assert plan.locator(".plan-test-rounds strong").text_content() == "3"
    assert plan.locator(".plan-test-warmup").text_content() == "另预热 1 轮 · 不保存结果"
    expected_jobs = str(len(raw["machines"][0]["systems"]) * 3)
    assert plan.locator(".plan-figure strong").text_content() == expected_jobs
    # Viewing a missing field does not modify the original YAML.
    set_editor_mode(page, "yaml")
    assert (
        "warmup" not in yaml.safe_load(page.locator("#yaml-editor").input_value())["benchmarks"][0]
    )
    set_editor_mode(page, "form")
    control.uncheck()
    assert plan.locator(".plan-test-warmup").text_content() == "预热已关闭"
    assert plan.locator(".plan-figure strong").text_content() == expected_jobs
    control.press("Space")
    assert control.is_checked()
    assert control.evaluate("input => document.activeElement === input")
    control.press("Space")
    assert not control.is_checked()
    open_step(page, "检查运行")
    table = page.locator("#config-body table")
    assert "正式轮数" in table.text_content() and "预热" in table.text_content()
    assert all("关闭" in row.text_content() for row in table.locator("tbody tr").all())
    assert table.evaluate("table => table.scrollWidth <= table.clientWidth")
    save_draft(page)
    page.wait_for_function("() => document.querySelector('#save-state').textContent === '已保存'")
    assert yaml.safe_load(server.existing.read_text())["benchmarks"][0]["warmup"] is False
    page.reload()
    page.locator("#config-body table").wait_for()
    open_step(page, "设置测试")
    assert not control.is_checked()
    set_editor_mode(page, "yaml")
    document = yaml.safe_load(page.locator("#yaml-editor").input_value())
    document["benchmarks"][0]["warmup"] = True
    page.locator("#yaml-editor").fill(yaml.safe_dump(document))
    set_editor_mode(page, "form")
    assert control.is_checked()
    assert plan.locator(".plan-test-rounds strong").text_content() == "3"
    assert plan.locator(".plan-test-warmup").text_content() == "另预热 1 轮 · 不保存结果"
    assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.parametrize("width", [1440, 768, 390])
def test_warmup_alignment_and_pointer_vs_keyboard_focus(browser_page, ui_server, width):
    from playwright.sync_api import expect

    page, server = browser_page, ui_server
    page.set_viewport_size({"width": width, "height": 1000})
    ready(page, server)
    load_existing(page, server)
    open_step(page, "设置测试")
    rounds = page.locator(".benchmark-rounds")
    formal = page.locator("#input-benchmarks-0-repetitions")
    control = page.get_by_role("checkbox", name="预热", exact=True)
    labels = rounds.locator(".field-label").all()
    assert abs(labels[0].bounding_box()["y"] - labels[1].bounding_box()["y"]) <= 1
    formal_box, switch_box = formal.bounding_box(), control.bounding_box()
    assert (
        abs(formal_box["y"] + formal_box["height"] / 2 - switch_box["y"] - switch_box["height"] / 2)
        <= 1
    )
    hints = rounds.locator(".field > .hint").all()
    assert abs(hints[0].bounding_box()["y"] - hints[1].bounding_box()["y"]) <= 1

    control.click()
    assert not control.is_checked()
    assert control.evaluate("input => getComputedStyle(input).outlineStyle") == "none"
    assert (
        page.locator(".warmup-setting").evaluate("label => getComputedStyle(label).outlineStyle")
        == "none"
    )
    rounds.locator(".warmup-field > .field-label").click()
    assert control.is_checked()
    assert control.evaluate("input => getComputedStyle(input).outlineStyle") == "none"

    formal.focus()
    expect(formal).to_have_css("border-width", "1px")
    expect(formal).to_have_css("border-color", "rgb(30, 64, 175)")
    expect(formal).to_have_css("border-radius", "10px")
    expect(formal).to_have_css("box-shadow", "rgba(59, 130, 246, 0.1) 0px 0px 0px 3px")
    expect(formal).to_have_css("outline-style", "solid")
    expect(formal).to_have_css("outline-offset", "-1px")
    page.keyboard.press("Tab")
    assert control.evaluate("input => document.activeElement === input")
    assert control.evaluate("input => input.matches(':focus-visible')")
    expect(control).to_have_css("outline-style", "solid")
    expect(formal).to_have_css("border-color", "rgb(118, 137, 166)")
    expect(formal).to_have_css("box-shadow", "none")
    control.press("Space")
    assert not control.is_checked()
    assert control.evaluate("input => input.matches(':focus-visible')")
    assert (
        page.locator(".warmup-setting").evaluate("label => getComputedStyle(label).outlineStyle")
        == "none"
    )
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.parametrize(
    "width,text_size", [(1280, 100), (1440, 100), (1920, 100), (390, 100), (720, 200)]
)
def test_input_focus_uses_single_halo_and_other_controls_use_neutral_focus(
    browser_page, ui_server, width, text_size
):
    from playwright.sync_api import expect

    page = browser_page
    page.set_viewport_size({"width": width, "height": 900})
    if text_size != 100:
        def apply_text_zoom(route):
            response = route.fetch()
            route.fulfill(
                response=response,
                body=response.text() + f"\nhtml {{ font-size: {text_size}%; }}",
            )

        page.route("**/static/style.css", apply_text_zoom)
    ready(page, ui_server)
    address = page.locator("#input-machines-0-ssh-host")
    address.click()
    expect(address).to_have_css("border-top-width", "1px")
    expect(address).to_have_css("border-top-color", "rgb(77, 131, 220)")
    expect(address).to_have_css("outline-style", "none")
    expect(address).to_have_css("box-shadow", "rgba(59, 130, 246, 0.1) 0px 0px 0px 3px")
    if (output := os.getenv("AUTOBENCH_UI_SCREENSHOTS")) and width == 1440:
        Path(output).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=Path(output) / "input-focus.png", animations="disabled")

    page.keyboard.press("Tab")
    select = page.locator(".machine-connection select:enabled").first
    select.focus()
    expect(select).to_have_css("outline-color", "rgb(71, 85, 105)")
    expect(select).to_have_css("outline-style", "solid")
    expect(select).to_have_css("box-shadow", "none")
    button = page.locator("#tab-runs")
    button.focus()
    expect(button).to_have_css("outline-color", "rgb(71, 85, 105)")
    expect(button).to_have_css("outline-style", "solid")


def test_warmup_status_shows_as_active_in_run_details(browser_page, ui_server):
    from autobench.storage import Store

    page, server = browser_page, ui_server
    store = Store(server.config.policy.state_dir)
    try:
        run_id = store.create(server.config)
        store.set_run_status(run_id, "running")
        store.update(store.jobs(run_id)[0]["id"], "warming_up")
    finally:
        store.close()
    ready(page, server)
    page.locator('[data-page="runs"]').click()
    page.get_by_role("button", name="查看详情", exact=True).click()
    detail = page.locator("#run-detail")
    detail.locator(".current-stage").wait_for()
    assert "预热中" in detail.locator(".current-stage").text_content()
    assert detail.locator(".stat").nth(1).text_content() == "1进行中"
    detail.get_by_text("按机器与系统查看任务", exact=True).click()
    detail.get_by_text("预热中", exact=True).wait_for()


@pytest.mark.parametrize("width", [1440, 390])
def test_run_detail_hides_resolved_reason_and_translates_recovery(browser_page, ui_server, width):
    from playwright.sync_api import expect

    from autobench.storage import Store

    page, server = browser_page, ui_server
    page.set_viewport_size({"width": width, "height": 900})
    english = "target boot verified after lost reboot acknowledgement; ready to resume"
    translated = "已确认目标系统启动，等待恢复控制。"
    store = Store(server.config.policy.state_dir)
    try:
        run_id = store.create(server.config)
        recovering, failed = store.jobs(run_id)
        store.set_run_status(run_id, "paused")
        store.update(recovering["id"], "paused", reason=english)
        esp_error = (
            "cannot read the GRUB menu for Boot0029: "
            "the EFI system partition for Boot0029 is not mounted"
        )
        store.update(failed["id"], "failed", reason=esp_error)
        ready(page, server)
        page.locator('[data-page="runs"]').click()
        page.get_by_role("button", name="查看详情", exact=True).click()
        detail = page.locator("#run-detail")
        notice = detail.locator(".run-reason").filter(has_text=translated)
        expect(notice).to_have_attribute("data-tone", "warning")
        failure = detail.locator(".run-reason").filter(
            has_text="无法读取 Boot0029 的 GRUB 菜单。目标 EFI 系统分区尚未挂载。"
        )
        expect(failure).to_have_attribute("data-tone", "error")
        expect(detail.get_by_text(esp_error, exact=True)).to_have_count(0)
        summary = detail.get_by_text("按机器与系统查看任务", exact=True)
        detail.get_by_role("button", name="复制完整运行 ID").press("Tab")
        expect(summary).to_be_focused()
        expect(summary).to_have_css("outline-style", "solid")
        expect(summary).to_have_css("outline-color", "rgb(71, 85, 105)")
        summary.press("Enter")
        expect(detail.get_by_text(translated, exact=True)).to_be_visible()
        expect(detail.get_by_text(english)).to_have_count(0)
        headers = detail.locator(".run-task-table thead tr")
        scroller = detail.get_by_role("region", name="机器与系统任务表")
        if width == 390:
            summary.press("Tab")
            expect(scroller).to_be_focused()
            scroller.evaluate("el => el.scrollLeft = el.scrollWidth")
            assert scroller.evaluate("el => el.scrollLeft") > 0
        column_edges = """rows => rows.map(row => [...row.cells].map(cell => {
            const { left, right } = cell.getBoundingClientRect();
            return [left, right];
        }))"""
        first, second = headers.evaluate_all(column_edges)
        assert first == second
        if (output := os.getenv("AUTOBENCH_UI_SCREENSHOTS")) and width == 1440:
            Path(output).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=Path(output) / "run-table.png", animations="disabled")

        # An older controller can retain the previous reason while the job runs.
        store.update(recovering["id"], "running")
        store.set_run_status(run_id, "running")
        expect(detail.locator(".current-stage")).to_contain_text("运行中")
        expect(notice).to_have_count(0)
        expect(detail.get_by_text(translated, exact=True)).to_have_count(0)
        if width == 390:
            expect(scroller).to_be_focused()
        expect(failure).to_be_visible()
        assert headers.evaluate_all(column_edges) == [first, first]
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    finally:
        store.close()


def test_unixbench_tests_selection_save_reload_and_yaml_roundtrip(browser_page, ui_server):
    page, server = browser_page, ui_server
    raw = server.config.model_dump(mode="json")
    raw["benchmarks"][0]["tests"] = ["custom-test"]
    raw["benchmarks"][0]["args"] = ["-c", "1", "pipe"]
    server.existing.write_text(yaml.safe_dump(raw))
    ready(page, server)
    load_existing(page, server)
    open_step(page, "设置测试")
    selected = page.get_by_role("list", name="已选测试项")
    assert selected.get_by_role("listitem").all_text_contents() == ["custom-test"]
    page.get_by_role("checkbox", name="execl · 程序替换吞吐量", exact=True).check()
    page.get_by_role("checkbox", name="shell · shell1、shell8、shell16", exact=True).check()
    page.get_by_label("其他测试名", exact=True).fill("hanoi")
    page.get_by_role("button", name="添加测试项", exact=True).click()
    page.get_by_role("button", name="移除测试项 custom-test", exact=True).click()
    set_editor_mode(page, "yaml")
    page.locator("#yaml-panel").wait_for()
    result = yaml.safe_load(page.locator("#yaml-editor").input_value())
    assert result["benchmarks"][0]["tests"] == ["execl", "shell", "hanoi"]
    assert result["benchmarks"][0]["args"] == ["-c", "1", "pipe"]
    set_editor_mode(page, "form")
    page.get_by_role("checkbox", name="execl · 程序替换吞吐量", exact=True).wait_for()
    assert page.get_by_role("checkbox", name="execl · 程序替换吞吐量").is_checked()
    save_draft(page)
    page.wait_for_function("() => document.querySelector('#save-state').textContent === '已保存'")
    assert yaml.safe_load(server.existing.read_text())["benchmarks"][0]["tests"] == [
        "execl",
        "shell",
        "hanoi",
    ]
    page.reload()
    shell = page.get_by_role("checkbox", name="shell · shell1、shell8、shell16")
    shell.wait_for()
    assert shell.is_checked()
    assert page.get_by_role("list", name="已选测试项").get_by_role(
        "listitem"
    ).all_text_contents() == ["execl", "shell", "hanoi"]
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
    page.get_by_role("checkbox", name="execl · 程序替换吞吐量", exact=True).uncheck()
    page.get_by_role("button", name="移除测试项 hanoi", exact=True).click()
    page.get_by_role("button", name="清空选择", exact=True).click()
    assert page.get_by_role("list", name="已选测试项").count() == 0
    set_editor_mode(page, "yaml")
    page.locator("#yaml-panel").wait_for()
    result = yaml.safe_load(page.locator("#yaml-editor").input_value())
    assert result["benchmarks"][0]["tests"] == []
    assert result["benchmarks"][0]["args"] == ["-c", "1", "pipe"]


def test_unixbench_tests_legacy_defaults_and_invalid_custom_name(browser_page, ui_server):
    page, server = browser_page, ui_server
    raw = server.config.model_dump(mode="json")
    raw["benchmarks"][0].pop("tests")
    server.existing.write_text(yaml.safe_dump(raw))
    ready(page, server)
    load_existing(page, server)
    open_step(page, "设置测试")
    assert page.locator(".unixbench-tests input:checked").count() == 0
    custom = page.get_by_label("其他测试名", exact=True)
    for invalid in ("", "-c", "execl shell"):
        custom.fill(invalid)
        page.get_by_role("button", name="添加测试项", exact=True).click()
        assert not custom.evaluate("input => input.validity.valid")
        assert page.get_by_role("list", name="已选测试项").count() == 0
    set_editor_mode(page, "yaml")
    page.locator("#yaml-panel").wait_for()
    assert (
        "tests" not in yaml.safe_load(page.locator("#yaml-editor").input_value())["benchmarks"][0]
    )


def test_saved_response_does_not_clear_newer_edits(browser_page, ui_server):
    page, server = browser_page, ui_server
    ready(page, server)
    load_existing(page, server)
    pending = []
    page.route("**/api/config/save", lambda route: pending.append(route))
    page.locator("#input-machines-0-ssh-username").fill("first-edit")
    save_draft(page)
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
    save_draft(page)
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
    open_step(page, "检查运行")
    page.clock.install(time=0)
    page.clock.pause_at(1000)
    click_primary(page)
    page.locator("#config-errors").wait_for()
    page.locator(".error-link").first.click()
    assert page.locator("#input-machines-0-ssh-port").get_attribute("aria-invalid") == "true"
    page.clock.run_for(5160)
    assert page.locator("#notice").is_hidden()
    assert page.locator("#config-errors").is_visible()
    assert page.locator(".field-error").is_visible()
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
    set_editor_mode(page, "yaml")
    page.locator("#yaml-panel").wait_for()
    page.locator("#yaml-editor").fill("machines: [\n")
    set_editor_mode(page, "form", succeeds=False)
    assert page.locator("#yaml-editor").input_value() == "machines: [\n"
    assert page.locator("#yaml-panel").is_visible()


def test_plan_preview_tracks_systems_rounds_and_yaml_changes(browser_page, ui_server):
    page, server = browser_page, ui_server
    ready(page, server)
    plan = page.locator("#config-plan")
    assert plan.locator(".plan-figure strong").text_content() == "0"
    raw = server.config.model_dump(mode="json")
    raw["machines"][0]["systems"][0]["grub_entry"] = "advanced>linux-custom"
    raw["benchmarks"][0]["repetitions"] = 3
    other = {**raw["benchmarks"][0], "id": "second-benchmark", "repetitions": 2}
    raw["benchmarks"].append(other)
    systems = [system for machine in raw["machines"] for system in machine["systems"]]
    system_count = len(systems)
    set_editor_mode(page, "yaml")
    page.locator("#yaml-editor").fill(yaml.safe_dump(raw))
    set_editor_mode(page, "form")
    plan.wait_for(state="visible")
    assert plan.locator(".plan-figure strong").text_content() == str(system_count * 5)
    assert plan.locator(".plan-route").get_by_role("listitem").all_text_contents() == [
        system["id"] for system in systems
    ]
    assert plan.locator(".plan-test").count() == len(raw["benchmarks"])
    plan.get_by_role("button", name="编辑测试", exact=True).click()
    page.locator("#input-benchmarks-0-repetitions").fill("4")
    assert plan.locator(".plan-figure strong").text_content() == str(system_count * 6)
    set_editor_mode(page, "yaml")
    page.locator("#yaml-panel").wait_for()
    assert plan.is_hidden()
    result = yaml.safe_load(page.locator("#yaml-editor").input_value())
    assert result["benchmarks"][0]["repetitions"] == 4
    saved_system = result["machines"][0]["systems"][0]
    assert saved_system["boot_entry"] == systems[0]["boot_entry"]
    assert saved_system["grub_entry"] == systems[0]["grub_entry"]
    assert "plan" not in result
    set_editor_mode(page, "form")
    plan.wait_for(state="visible")
    open_step(page, "运行设置")
    assert plan.is_visible()
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_mobile_run_row_keeps_details_action_and_dates_visible(browser_page, ui_server):
    from autobench.storage import Store

    page, server = browser_page, ui_server
    store = Store(server.config.policy.state_dir)
    run_id = store.create(server.config)
    store.close()
    page.set_viewport_size({"width": 390, "height": 844})
    ready(page, server)
    page.locator('[data-page="runs"]').click()
    action = page.get_by_role("button", name="查看详情", exact=True)
    action.wait_for()
    cells = page.locator("#runs-list tbody tr").first.locator("td")
    assert cells.count() == 4
    assert run_id in cells.nth(0).text_content()
    assert cells.nth(1).text_content()
    assert cells.nth(2).text_content()
    for cell in (cells.nth(0), cells.nth(2), action):
        box = cell.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= 390
    assert page.locator("#runs-list").evaluate("e => e.scrollWidth === e.clientWidth")
    action.click()
    page.locator("#run-detail").get_by_text(run_id, exact=True).wait_for()


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
    open_system(page, "os1")
    page.get_by_label("单独设置此系统的连接地址或用户名", exact=True).uncheck()
    page.get_by_text("系统高级设置", exact=True).click()
    page.get_by_label("连接代理", exact=True).select_option("inherit")
    set_editor_mode(page, "yaml")
    page.locator("#yaml-panel").wait_for()
    result = yaml.safe_load(page.locator("#yaml-editor").input_value())
    assert result["machines"][0]["systems"][0]["ssh"] == {
        "password": None,
        "client_keys": [],
        "host_key_alias": None,
    }
    assert result["machines"][0]["ssh"] == raw["machines"][0]["ssh"]
    set_editor_mode(page, "form")
    page.locator("#config-body").wait_for()
    page.get_by_label("单独设置此系统的认证方式", exact=True).uncheck()
    set_editor_mode(page, "yaml")
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


def test_checking_saved_yaml_skips_rewriting_and_tab_leaves_editor(browser_page, ui_server):
    page, server = browser_page, ui_server
    ready(page, server, startup_path=server.existing)
    requests = []
    page.on("request", lambda request: requests.append(request.url))
    set_editor_mode(page, "yaml")
    editor = page.locator("#yaml-editor")
    editor.press("Tab")
    assert page.evaluate("document.activeElement.id") != "yaml-editor"
    assert page.locator("#primary-action").text_content() == "检查配置"
    click_primary(page)
    page.get_by_text("配置、源码包及本地 SSH 文件检查通过，可以开始测试。", exact=True).wait_for()
    assert not any(url.endswith("/api/config/save") for url in requests)
    assert not server.world.starts


def test_save_as_conflict_preserves_original_target(browser_page, ui_server):
    page, server = browser_page, ui_server
    ready(page, server)
    original = server.existing.read_bytes()
    page.locator("#input-machines-0-ssh-host").fill("changed.example")
    page.locator("#file-menu summary").click()
    page.locator("#save-as").click()
    page.locator("#save-path").fill(str(server.existing))
    page.locator("#path-confirm").click()
    page.locator("#path-error").wait_for()
    assert "目标文件已存在" in page.locator("#path-error").inner_text()
    assert server.existing.read_bytes() == original
    page.get_by_role("button", name="取消", exact=True).click()
    assert page.locator("#file-location").text_content() == str(server.root / "autobench.yaml")
    assert page.locator("#input-machines-0-ssh-host").input_value() == "changed.example"
    save_draft(page)
    page.wait_for_function("() => document.querySelector('#save-state').textContent === '已保存'")
    assert (
        yaml.safe_load((server.root / "autobench.yaml").read_text())["machines"][0]["ssh"]["host"]
        == "changed.example"
    )


def test_invalid_nested_document_keeps_source_accessible(browser_page, ui_server):
    page = browser_page
    ready(page, ui_server)
    text = "version: 1\nmachines: [null]\nbenchmarks: []\n"
    page.locator("#import").set_input_files(
        {"name": "broken.yaml", "mimeType": "text/yaml", "buffer": text.encode()}
    )
    page.get_by_text("配置结构需要修正", exact=True).wait_for()
    set_editor_mode(page, "yaml")
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
        "sessionStorage.setItem('autobench-page', 'runs');"
        "sessionStorage.setItem('autobench-selection', "
        + json.dumps(json.dumps({"id": run_id, "stateDir": str(server.config.policy.state_dir)}))
        + ")"
    )
    ready(page, server)
    page.locator("#tab-runs").click()
    page.get_by_role("button", name="继续处理取消", exact=True).click()
    page.wait_for_function(
        "() => document.querySelector('#run-detail').textContent.includes('运行已取消')"
    )
    assert not server.world.starts


@pytest.mark.parametrize("width", [1440, 390])
def test_host_key_switch_preserves_layout_values_and_system_override(browser_page, ui_server, width):
    page, server = browser_page, ui_server
    page.set_viewport_size({"width": width, "height": 1000})
    raw = server.config.model_dump(mode="json")
    raw["machines"][0]["ssh"]["host_key_alias"] = "saved-alias"
    server.existing.write_text(yaml.safe_dump(raw))
    ready(page, server)
    load_existing(page, server)
    known_hosts = page.locator("#input-machines-0-ssh-known_hosts")
    alias = page.locator("#input-machines-0-ssh-host_key_alias")
    ignore = page.get_by_label("忽略主机密钥检查", exact=True)
    original = known_hosts.input_value()
    page.evaluate("document.fonts.ready")
    sections = page.locator(".machine-connection-section")
    measure = "elements => elements.map(e => [e.offsetTop, e.offsetWidth, e.offsetHeight])"
    before = sections.evaluate_all(measure)
    ignore.check()
    assert known_hosts.is_visible() and known_hosts.is_disabled()
    assert alias.is_visible() and alias.is_disabled()
    browse = page.locator(".machine-known-hosts .browse-button")
    assert browse.is_disabled()
    assert page.locator(".machine-alias-info").is_hidden()
    assert page.get_by_text("已忽略主机密钥检查", exact=True).count() == 0
    assert sections.evaluate_all(measure) == before
    page.evaluate("getSelection().removeAllRanges()")
    for control in (known_hosts, alias, browse):
        control.scroll_into_view_if_needed()
        box = control.bounding_box()
        x = box["x"] + box["width"] / 2
        y = box["y"] + box["height"] / 2
        assert page.evaluate(
            "([x, y]) => document.elementFromPoint(x, y).matches('.machine-host-keys:disabled')",
            [x, y],
        )
        page.mouse.click(x, y)
        assert page.evaluate("getSelection().rangeCount === 0")
    assert page.locator(".machine-host-keys :focus, dialog[open]").count() == 0
    ignore.uncheck()
    assert known_hosts.is_enabled() and known_hosts.input_value() == original
    assert alias.is_enabled() and alias.input_value() == "saved-alias"
    assert page.locator(".machine-alias-info").is_visible()
    assert sections.evaluate_all(measure) == before
    ignore.check()
    open_system(page, "os1")
    page.get_by_text("系统高级设置", exact=True).click()
    page.get_by_label("主机密钥检查", exact=True).select_option("verify")
    assert page.locator("#input-machines-0-systems-0-ssh-known_hosts").input_value() == original
    assert page.get_by_text("已忽略主机密钥检查", exact=True).count() == 0
    set_editor_mode(page, "yaml")
    page.locator("#yaml-panel").wait_for()
    data = yaml.safe_load(page.locator("#yaml-editor").input_value())
    assert data["machines"][0]["ssh"]["verify_host_key"] is False
    assert data["machines"][0]["systems"][0]["ssh"]["verify_host_key"] is True
    set_editor_mode(page, "form")
    page.locator("#config-body").wait_for()
    page.get_by_label("主机密钥检查", exact=True).select_option("inherit")
    assert page.locator("#input-machines-0-systems-0-ssh-known_hosts").count() == 0
    set_editor_mode(page, "yaml")
    page.locator("#yaml-panel").wait_for()
    data = yaml.safe_load(page.locator("#yaml-editor").input_value())
    assert "verify_host_key" not in data["machines"][0]["systems"][0]["ssh"]
    assert data["machines"][0]["ssh"]["known_hosts"] == original
    assert data["machines"][0]["ssh"]["host_key_alias"] == "saved-alias"


def test_ignore_host_key_allows_connection_test_and_validation_without_hosts(
    browser_page, ui_server
):
    page, server = browser_page, ui_server
    ready(page, server)
    saved_path = server.root / "autobench.yaml"
    saved_text = saved_path.read_bytes()
    page.locator("#input-machines-0-ssh-host").fill("localhost")
    page.locator("#input-machines-0-ssh-username").fill("bench")
    page.locator("#input-machines-0-ssh-password").fill("test-secret")
    page.locator("#input-machines-0-ssh-known_hosts").fill(str(server.root / "absent-hosts"))
    page.get_by_label("忽略主机密钥检查", exact=True).check()
    page.get_by_role("button", name="连接测试", exact=True).click()
    page.locator('.connection-test-status[data-phase="success"]').wait_for()
    assert saved_path.read_bytes() == saved_text
    data = server.config.model_dump(mode="json")
    data["machines"][0]["ssh"].update(verify_host_key=False, known_hosts="absent-hosts")
    set_editor_mode(page, "yaml")
    page.locator("#yaml-panel").wait_for()
    page.locator("#yaml-editor").fill(yaml.safe_dump(data))
    click_primary(page)
    page.wait_for_function(
        "() => document.querySelector('#primary-action').textContent.includes('开始测试')"
    )
    assert (
        yaml.safe_load((server.root / "autobench.yaml").read_text())["machines"][0]["ssh"][
            "verify_host_key"
        ]
        is False
    )


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("value", ["0001", "", "00AB"])
def test_boot_entry_identification_spinner_choices_and_selection(
    browser_page, ui_server, width, value
):
    page, server = browser_page, ui_server
    page.set_viewport_size({"width": width, "height": 1000})
    ready(page, server)
    load_existing(page, server)
    open_system(page, "os1")
    assert page.get_by_role("button", name="连接测试", exact=True).count() == 0
    entry = page.locator("#input-machines-0-systems-0-boot_entry")
    pending = []
    page.route("**/api/systems/boot-entries", lambda route: pending.append(route))
    identify = page.get_by_role("button", name="自动识别", exact=True)
    identify.click()
    status = page.locator('.boot-entry-status[data-phase="running"]')
    status.wait_for()
    assert identify.is_disabled()
    identify.evaluate("e => e.click()")
    assert len(pending) == 1
    assert (
        status.locator(".status-icon").evaluate("e => getComputedStyle(e).animationName")
        == "status-spin"
    )
    input_box, button_box, icon_box = (
        entry.bounding_box(),
        identify.bounding_box(),
        status.locator(".status-icon").bounding_box(),
    )
    assert input_box["x"] < button_box["x"] < icon_box["x"]
    assert button_box["y"] <= icon_box["y"] < button_box["y"] + button_box["height"]
    entry.fill(value)
    response = pending[0].fetch()
    pending[0].fulfill(response=response)
    status = page.locator('.boot-entry-status[data-phase="success"]')
    status.wait_for()
    assert status.locator(".status-icon svg[aria-hidden=true]").count() == 1
    assert status.evaluate("e => getComputedStyle(e).color") == "rgb(22, 101, 52)"
    assert entry.evaluate("e => e.tagName") == "SELECT"
    assert entry.input_value() == value
    assert entry.locator("option:enabled").evaluate_all("items => items.map(e => e.value)") == [
        "0001",
        "0002",
    ]
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    entry.select_option("0002")
    open_system(page, "os2")
    assert page.locator(".boot-entry-controls input").count() == 1
    open_system(page, "os1")
    assert entry.evaluate("e => e.tagName") == "SELECT" and entry.input_value() == "0002"
    set_editor_mode(page, "yaml")
    page.locator("#yaml-panel").wait_for()
    systems = yaml.safe_load(page.locator("#yaml-editor").input_value())["machines"][0]["systems"]
    assert len(systems) == 2 and systems[0]["boot_entry"] == "0002"
    assert (
        yaml.safe_load(server.existing.read_text())["machines"][0]["systems"][0]["boot_entry"]
        == "0001"
    )


@pytest.mark.parametrize(
    ("width", "text_size"), [(1440, 100), (390, 100), (320, 100), (1440, 200), (390, 200)]
)
def test_long_boot_entry_label_keeps_select_size(browser_page, ui_server, width, text_size):
    from playwright.sync_api import expect

    page, server = browser_page, ui_server
    page.set_viewport_size({"width": width, "height": 1000})
    page.emulate_media(reduced_motion="reduce")
    if text_size == 200:

        def apply_text_zoom(route):
            response = route.fetch()
            route.fulfill(response=response, body=response.text() + "\nhtml { font-size: 200%; }")

        page.route("**/static/style.css", apply_text_zoom)
    ready(page, server, startup_path=server.existing)
    open_system(page, "os1")
    label = "UEFI PXEv6 (MAC:001122334455) " + "Pci(0x0,0x0)/" * 16
    page.route(
        "**/api/systems/boot-entries",
        lambda route: route.fulfill(
            json={
                "status": "completed",
                "result": {
                    "entries": [
                        {"id": "0001", "label": "Linux", "active": True},
                        {"id": "0002", "label": label, "active": True},
                    ]
                },
            }
        ),
    )
    identify = page.get_by_role("button", name="自动识别", exact=True)
    identify.click()
    entry = page.get_by_role("combobox", name="UEFI 启动项")
    expect(entry).to_have_value("0001")
    short_box = entry.bounding_box()
    button_box = identify.bounding_box()
    short_scroll_width = entry.evaluate("el => el.scrollWidth")
    entry.click()
    menu = page.get_by_role("listbox", name="UEFI 启动项")
    option = (menu if menu.is_visible() else entry).get_by_role(
        "option", name=f"Boot0002 · {label}", exact=True
    )
    expect(option).to_be_visible()
    option.click()
    expect(entry).to_have_value("0002")
    long_box = entry.bounding_box()
    assert long_box["width"] == short_box["width"]
    assert long_box["height"] == short_box["height"]
    assert identify.bounding_box() == button_box
    assert entry.evaluate("el => el.scrollWidth") == short_scroll_width
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.parametrize("failure", ["network", "request", "operation", "empty"])
def test_boot_entry_identification_failure_preserves_input_and_allows_retry(
    browser_page, ui_server, failure
):
    page, server = browser_page, ui_server
    page.set_viewport_size({"width": 390, "height": 1000})
    ready(page, server)
    load_existing(page, server)
    open_system(page, "os1")
    entry = page.locator("#input-machines-0-systems-0-boot_entry")
    entry.fill("00AB")
    message = "启动项读取失败：" + "x" * 180

    def fail(route):
        if failure == "network":
            route.abort()
        elif failure == "request":
            route.fulfill(status=422, json={"detail": message})
        elif failure == "operation":
            route.fulfill(json={"status": "failed", "error": message})
        else:
            route.fulfill(json={"status": "completed", "result": {"entries": []}})

    page.route("**/api/systems/boot-entries", fail)
    identify = page.get_by_role("button", name="自动识别", exact=True)
    identify.click()
    status = page.locator('.boot-entry-status[data-phase="error"]')
    status.wait_for()
    assert status.locator(".status-icon svg[aria-hidden=true]").count() == 1
    assert status.evaluate("e => getComputedStyle(e).color") == "rgb(180, 35, 24)"
    assert status.locator(".status-message").text_content()
    assert entry.evaluate("e => e.tagName") == "INPUT" and entry.input_value() == "00AB"
    assert not identify.is_disabled()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.unroute("**/api/systems/boot-entries", fail)
    identify.click()
    page.locator('.boot-entry-status[data-phase="running"]').wait_for()
    assert page.locator(".boot-entry-status .status-message").count() == 0
    page.locator('.boot-entry-status[data-phase="success"]').wait_for()
    assert entry.evaluate("e => e.tagName") == "SELECT" and entry.input_value() == "00AB"
    page.route("**/api/systems/boot-entries", fail)
    identify.click()
    page.locator('.boot-entry-status[data-phase="error"]').wait_for()
    assert entry.evaluate("e => e.tagName") == "SELECT" and entry.input_value() == "00AB"


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("boot_entry", ["", "0002"])
def test_grub_auto_default_manual_selection_and_clear(browser_page, ui_server, width, boot_entry):
    page, server = browser_page, ui_server
    document = yaml.safe_load(server.existing.read_text())
    del document["machines"][0]["systems"][0]["grub_entry"]
    document["machines"][0]["systems"][0]["boot_entry"] = boot_entry
    server.existing.write_text(yaml.safe_dump(document))
    ready(page, server)
    page.set_viewport_size({"width": width, "height": 1000})
    load_existing(page, server)
    open_system(page, "os1")
    match = "**/api/systems/boot-entries"
    result = {
        "entries": [
            {"id": "0001", "label": "Shared GRUB", "active": True},
            {"id": "0002", "label": "Other UEFI entry", "active": True},
        ],
        "grub": {
            "boot_entry": "0001",
            "default_entry": "linux-default",
            "entries": [
                {"id": "linux-default", "label": "Linux", "selectable": True},
                {"id": "advanced>linux-old", "label": "高级选项 › 旧内核", "selectable": True},
                {"id": "firmware", "label": "Firmware", "selectable": False},
            ],
            "error": None,
        },
    }

    def identify(route):
        assert "boot_entry" not in route.request.post_data_json
        route.fulfill(json={"status": "completed", "result": result})

    page.route(match, identify)
    detect = page.get_by_role("button", name="自动识别", exact=True)
    grub = page.get_by_label("GRUB 启动项", exact=True)
    detect.click()
    page.locator('.boot-entry-status[data-phase="success"]').wait_for()
    uefi = page.locator("#input-machines-0-systems-0-boot_entry")
    assert uefi.input_value() == boot_entry
    assert grub.input_value() == "linux-default"
    assert grub.locator("option[value=firmware]").is_disabled()
    uefi.select_option("0001")
    uefi.select_option("0002")
    assert grub.input_value() == "linux-default"
    grub.select_option("advanced>linux-old")
    detect.click()
    page.locator('.boot-entry-status[data-phase="success"]').wait_for()
    assert grub.input_value() == "advanced>linux-old"
    page.get_by_role("button", name="手动填写", exact=True).click()
    grub.fill("advanced>linux-custom")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    set_editor_mode(page, "yaml")
    page.locator("#yaml-panel").wait_for()
    config = yaml.safe_load(page.locator("#yaml-editor").input_value())
    assert config["machines"][0]["systems"][0]["grub_entry"] == "advanced>linux-custom"
    set_editor_mode(page, "form")
    open_system(page, "os1")
    detect.click()
    page.locator('.boot-entry-status[data-phase="success"]').wait_for()
    assert grub.input_value() == "advanced>linux-custom"
    grub.select_option("")
    detect.click()
    page.locator('.boot-entry-status[data-phase="success"]').wait_for()
    assert grub.input_value() == ""
    set_editor_mode(page, "yaml")
    page.locator("#yaml-panel").wait_for()
    config = yaml.safe_load(page.locator("#yaml-editor").input_value())
    assert config["machines"][0]["systems"][0]["grub_entry"] is None


@pytest.mark.parametrize("old_failure", [False, True])
def test_boot_entry_identification_uses_overrides_and_ignores_outdated_results(
    browser_page, ui_server, old_failure
):
    page, server = browser_page, ui_server
    ready(page, server)
    load_existing(page, server)
    open_system(page, "os1")
    page.get_by_label("单独设置此系统的连接地址或用户名", exact=True).check()
    host = page.locator("#input-machines-0-systems-0-ssh-host")
    host.fill("old-host")
    entry = page.locator("#input-machines-0-systems-0-boot_entry")
    entry.fill("00AB")
    pending = []
    page.route("**/api/systems/boot-entries", lambda route: pending.append(route))
    identify = page.get_by_role("button", name="自动识别", exact=True)
    identify.click()
    page.locator('.boot-entry-status[data-phase="running"]').wait_for()
    assert pending[0].request.post_data_json["ssh"]["host"] == "old-host"
    host.fill("new-host")
    page.locator('.boot-entry-status[data-phase="stale"]').wait_for()
    assert not identify.is_disabled()
    identify.click()
    page.locator('.boot-entry-status[data-phase="running"]').wait_for()
    assert pending[1].request.post_data_json["ssh"]["host"] == "new-host"
    pending[1].fulfill(
        json={
            "status": "completed",
            "result": {"entries": [{"id": "0002", "label": "New Linux", "active": True}]},
        }
    )
    page.locator('.boot-entry-status[data-phase="success"]').wait_for()
    older = (
        {"status": "failed", "error": "旧连接失败"}
        if old_failure
        else {
            "status": "completed",
            "result": {"entries": [{"id": "0001", "label": "Old Linux", "active": True}]},
        }
    )
    with page.expect_response("**/api/systems/boot-entries"):
        pending[0].fulfill(json=older)
    page.wait_for_timeout(50)
    assert entry.locator("option:enabled").evaluate_all("items => items.map(e => e.value)") == [
        "0002"
    ]
    host.fill("third-host")
    page.locator('.boot-entry-status[data-phase="stale"]').wait_for()
    assert entry.evaluate("e => e.tagName") == "INPUT" and entry.input_value() == "00AB"


@pytest.mark.parametrize("width", [1440, 768, 390, 320])
def test_step_actions_share_navigation_through_review(browser_page, ui_server, width):
    page = browser_page
    page.set_viewport_size({"width": width, "height": 640})
    ready(page, ui_server)
    load_existing(page, ui_server)
    nav = page.locator("#wizard-steps")
    group = nav.get_by_role("group", name="步骤操作")
    assert page.locator(".topbar #primary-action, .action-bar").count() == 0

    open_step(page, "选择系统")
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    reveal_steps(page)
    nav.evaluate("el => Promise.all(el.getAnimations().map(animation => animation.finished))")
    back = group.get_by_role("button", name="上一步", exact=True).bounding_box()
    next_button = group.get_by_role("button", name="下一步", exact=True)
    next_step = next_button.bounding_box()
    nav_box = nav.bounding_box()
    assert back["y"] == next_step["y"]
    assert back["x"] + back["width"] <= next_step["x"]
    assert next_step["x"] + next_step["width"] <= nav_box["x"] + nav_box["width"]
    assert next_step["height"] >= 44
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

    page.mouse.move(0, 0)
    assert next_button.evaluate(
        "button => getComputedStyle(button).backgroundImage.startsWith('linear-gradient')"
    )
    next_button.hover()
    assert next_button.evaluate("button => getComputedStyle(button).backgroundImage") == "none"

    open_step(page, "检查运行")
    click_primary(page)
    page.wait_for_function(
        "() => document.querySelector('#primary-action').textContent.includes('开始测试')"
    )
    reveal_steps(page)
    assert group.get_by_role("button", name="开始测试", exact=True).is_visible()


@pytest.mark.parametrize("width", [1440, 1024])
def test_desktop_step_sidebar_stays_aligned_when_returning_from_bottom(
    browser_page, ui_server, width
):
    page = browser_page
    page.set_viewport_size({"width": width, "height": 720})
    ready(page, ui_server, startup_path=ui_server.existing)
    open_step(page, "设置测试")

    sidebar = page.get_by_role("navigation", name="配置步骤")
    top = sidebar.bounding_box()["y"]
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    assert page.evaluate("scrollY") > 0
    bottom = sidebar.bounding_box()["y"]
    open_step(page, "连接机器")
    assert page.evaluate("scrollY") == 0
    assert bottom == pytest.approx(top, abs=1)
    assert sidebar.bounding_box()["y"] == pytest.approx(bottom, abs=1)


@pytest.mark.parametrize("width", [1440, 1101, 1100, 768, 700, 390, 320])
def test_steps_beside_form_or_in_mobile_picker(browser_page, ui_server, width):
    page = browser_page
    page.set_viewport_size({"width": width, "height": 900})
    ready(page, ui_server, startup_path=ui_server.existing)
    picker = page.locator(".topbar #step-picker")
    navigation = page.locator("#wizard-steps")
    assert picker.is_visible() == (width < 768)
    for index in range(5):
        reveal_steps(page)
        navigation.evaluate(
            "el => Promise.all(el.getAnimations().map(animation => animation.finished))"
        )
        nav_box = navigation.bounding_box()
        nav_step = navigation.locator(".wizard-step").nth(index)
        assert nav_step.bounding_box()["height"] >= 44
        if width < 768:
            picker_box = picker.bounding_box()
            assert nav_box["y"] > picker_box["y"] + picker_box["height"]
            assert (
                picker_box["x"] + picker_box["width"]
                <= page.locator("#file-menu summary").bounding_box()["x"]
            )
            assert nav_box["x"] >= 0 and nav_box["x"] + nav_box["width"] <= width
        else:
            form = page.locator("#config-body").bounding_box()
            assert nav_box["x"] + nav_box["width"] <= form["x"]
            assert nav_box["y"] == pytest.approx(form["y"], abs=1)
        nav_step.click()
        assert navigation.locator(".wizard-step[aria-current=step]").count() == 1
        assert navigation.is_visible() == (width >= 768)
        assert picker.get_attribute("aria-expanded") == "false"
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

    set_editor_mode(page, "yaml")
    assert navigation.is_hidden()
    assert picker.is_hidden()
    assert page.locator("#prev-step").is_hidden()
    assert (
        page.locator("#yaml-panel .yaml-help")
        .get_by_role("button", name="检查配置", exact=True)
        .is_visible()
    )
    page.locator("#tab-runs").click()
    assert page.locator("#primary-action").is_hidden()


def test_save_draft_shortcut_while_editing(browser_page, ui_server):
    page, server = browser_page, ui_server
    ready(page, server)
    host = page.locator("#input-machines-0-ssh-host")
    host.fill("updated.example")
    host.press("Control+s")
    page.wait_for_function("() => document.querySelector('#save-state').textContent === '已保存'")
    assert host.evaluate("e => e === document.activeElement")
    document = yaml.safe_load((server.root / "autobench.yaml").read_text())
    assert document["machines"][0]["ssh"]["host"] == "updated.example"


@pytest.mark.parametrize("width", [1440, 768])
def test_step_keyboard_focus_survives_render(browser_page, ui_server, width):
    page = browser_page
    page.set_viewport_size({"width": width, "height": 900})
    ready(page, ui_server, startup_path=ui_server.existing)
    step = page.locator(".wizard-step").nth(3)
    step.focus()
    page.keyboard.press("Space")
    assert page.locator(".wizard-step[aria-current=step]").get_attribute("id") == "wizard-step-3"
    assert page.evaluate("document.activeElement.id") == "wizard-step-3"
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.id") == "wizard-step-4"
    page.keyboard.press("Enter")
    assert page.evaluate("document.activeElement.id") == "wizard-step-4"
    assert page.locator(".wizard-step[aria-current=step]").get_attribute("id") == "wizard-step-4"
    page.locator("#prev-step").focus()
    page.keyboard.press("Enter")
    assert page.locator("#config-body h2").first.evaluate("e => e === document.activeElement")
    assert "运行设置" in page.locator(".wizard-step[aria-current=step]").text_content()


def test_step_picker_supports_keyboard_and_restores_focus(browser_page, ui_server):
    page = browser_page
    page.set_viewport_size({"width": 390, "height": 844})
    ready(page, ui_server, startup_path=ui_server.existing)
    picker = page.get_by_role("button", name="配置步骤 1/5 · 连接机器", exact=True)
    picker.press("Enter")
    assert page.evaluate("document.activeElement.id") == "wizard-step-0"
    page.keyboard.press("Escape")
    assert picker.get_attribute("aria-expanded") == "false"
    assert page.evaluate("document.activeElement.id") == "step-picker"
    picker.press("Space")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.id") == "wizard-step-1"
    page.keyboard.press("Enter")
    assert page.evaluate("document.activeElement.id") == "step-picker"
    assert page.locator("#wizard-steps").is_hidden()
    assert page.get_by_role("button", name="配置步骤 2/5 · 选择系统", exact=True).is_visible()
    reveal_steps(page)
    page.locator("#prev-step").press("Enter")
    assert page.locator("#wizard-steps").is_hidden()
    assert page.get_by_role("button", name="配置步骤 1/5 · 连接机器", exact=True).is_visible()


@pytest.mark.parametrize("width", [1440, 768, 390])
def test_five_steps_preserve_existing_config_and_fit_viewport(browser_page, ui_server, width):
    page, server = browser_page, ui_server
    page.set_viewport_size({"width": width, "height": 1000})
    original = server.existing.read_text()
    ready(page, server)
    load_existing(page, server)
    steps = ["连接机器", "选择系统", "设置测试", "运行设置", "检查运行"]
    assert page.locator("#workbench-switch").count() == 0
    assert page.locator("#mode-toggle").get_attribute("aria-checked") == "false"
    assert page.locator(".wizard-step").count() == 5
    for index, name in enumerate(steps):
        open_step(page, name)
        assert (
            page.locator(".wizard-step[aria-current=step]").text_content() == str(index + 1) + name
        )
        assert page.locator("#config-plan").is_visible() == (index < 4)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert (page.locator("#prev-step").get_attribute("hidden") is None) == (index > 0)
        assert page.locator("#primary-action").text_content().strip() == (
            "下一步" if index < 4 else "检查配置"
        )
        if name == "设置测试":
            assert page.locator("#input-policy-output_dir").count() == 0
        if name == "运行设置":
            page.get_by_text("高级调度设置", exact=True).click()
            assert page.locator("#input-policy-output_dir").is_visible()
            page.locator("#input-version").wait_for(state="visible")
    save_draft(page)
    page.wait_for_function(
        "() => !document.querySelector('#secondary-action').classList.contains('busy')"
    )
    assert server.existing.read_text() == original
    page.reload()
    page.locator("#config-body table").wait_for()
    assert page.locator(".wizard-step[aria-current=step]").text_content() == "5检查运行"
    assert page.locator(".wizard-step").count() == 5


@pytest.mark.parametrize("width", [1920, 1440, 1280, 1279, 1024, 768])
def test_plan_aligns_with_each_wizard_card(browser_page, ui_server, width):
    page = browser_page
    page.set_viewport_size({"width": width, "height": 900})
    ready(page, ui_server, startup_path=ui_server.existing)
    card = page.locator("#config-body > .panel, .system-content > .panel")
    plan = page.locator("#config-plan")

    def assert_plan_alignment():
        card_box = card.bounding_box()
        plan_box = plan.bounding_box()
        if width >= 1280:
            assert plan_box["y"] == pytest.approx(card_box["y"], abs=1)
            assert plan_box["x"] > card_box["x"] + card_box["width"]
        else:
            assert plan_box["x"] == pytest.approx(card_box["x"], abs=1)
            assert plan_box["y"] >= card_box["y"] + card_box["height"]

    assert_plan_alignment()
    open_step(page, "选择系统")
    assert_plan_alignment()
    page.get_by_role("button", name="编辑系统配置 os1", exact=True).click()
    assert_plan_alignment()
    for step in ["设置测试", "运行设置"]:
        open_step(page, step)
        assert_plan_alignment()

    open_step(page, "连接机器")
    for _ in range(2):
        page.get_by_role("button", name="添加机器", exact=True).click()
    assert_plan_alignment()


@pytest.mark.parametrize("width", [851, 320])
def test_picker_tiles_fit_names_without_overflowing_config(browser_page, ui_server, width):
    page, server = browser_page, ui_server
    document = yaml.safe_load(server.existing.read_text())
    for key in ("machines", "benchmarks"):
        item = document[key][0]
        document[key] = [{**item, "id": name} for name in ("short", "W" * 64)]
    server.existing.write_text(yaml.safe_dump(document))
    page.set_viewport_size({"width": width, "height": 900})
    ready(page, server, startup_path=server.existing)
    page.evaluate("document.fonts.ready")

    for step in ("连接机器", "选择系统", "设置测试"):
        open_step(page, step)
        tiles = page.locator(".object-tabs .object-tab")
        short, long = [tile.bounding_box() for tile in tiles.all()]
        form = page.locator("#config-body").bounding_box()
        assert short["width"] < long["width"]
        assert long["x"] + long["width"] <= form["x"] + form["width"]
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.parametrize("width", [1440, 768, 390])
def test_machine_drag_and_keyboard_sort_preserve_config(browser_page, ui_server, width):
    page, server = browser_page, ui_server
    page.set_viewport_size({"width": width, "height": 1000})
    ready(page, server)
    load_existing(page, server)
    original_machine = yaml.safe_load(server.existing.read_text())["machines"][0]
    for number in [2, 3]:
        page.get_by_role("button", name="添加机器", exact=True).click()
        page.locator(f"#input-machines-{number - 1}-ssh-host").fill(f"host-{number}")
    tabs = page.locator(".machine-picker .chip-button")
    assert page.locator(".object-controls").count() == 0
    tabs.nth(0).drag_to(tabs.nth(2))
    assert tabs.all_text_contents() == ["server02", "server03", "server01"]
    assert page.locator(".machine-picker .active").text_content() == "server03"
    assert page.locator("#input-machines-1-ssh-host").input_value() == "host-3"
    tabs.nth(2).drag_to(tabs.nth(0))
    assert tabs.all_text_contents() == ["server01", "server02", "server03"]
    tabs.nth(2).drag_to(tabs.nth(0))
    assert tabs.all_text_contents() == ["server03", "server01", "server02"]
    tabs.nth(0).press("Alt+ArrowRight")
    assert tabs.all_text_contents() == ["server01", "server03", "server02"]
    assert tabs.nth(1).evaluate("e => e === document.activeElement")
    assert page.locator(".machine-picker .active").text_content() == "server03"
    assert page.locator("#input-machines-1-ssh-host").input_value() == "host-3"
    tabs.nth(1).press("Alt+ArrowLeft")
    tabs.nth(0).press("Alt+ArrowLeft")  # Moving beyond the first position is a no-op.
    assert tabs.all_text_contents() == ["server03", "server01", "server02"]
    assert page.locator(".dragging, .drop-target").count() == 0
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    save_draft(page)
    page.wait_for_function("() => document.querySelector('#save-state').textContent === '已保存'")
    saved = yaml.safe_load(server.existing.read_text())
    assert [m["id"] for m in saved["machines"]] == ["server03", "server01", "server02"]
    assert saved["machines"][1] == original_machine
    assert saved["machines"][0]["ssh"]["host"] == "host-3"
    assert saved["machines"][2]["ssh"]["host"] == "host-2"
    page.reload()
    page.wait_for_function(
        "() => document.querySelectorAll('.machine-picker .chip-button').length === 3"
    )
    assert tabs.all_text_contents() == ["server03", "server01", "server02"]
    set_editor_mode(page, "yaml")
    assert yaml.safe_load(page.locator("#yaml-editor").input_value()) == saved


@pytest.mark.parametrize("width", [1440, 768, 390])
def test_machine_drag_across_rows_and_cancel(browser_page, ui_server, width):
    page, server = browser_page, ui_server
    page.set_viewport_size({"width": width, "height": 1000})
    ready(page, server)
    load_existing(page, server)
    for _ in range(13):
        page.get_by_role("button", name="添加机器", exact=True).click()
    tabs = page.locator(".machine-picker .chip-button")
    before = tabs.all_text_contents()
    assert tabs.nth(0).bounding_box()["y"] < tabs.nth(13).bounding_box()["y"]
    tabs.nth(13).drag_to(tabs.nth(0))
    assert tabs.all_text_contents() == [before[-1], *before[:-1]]
    assert page.locator(".machine-picker .active").text_content() == "server14"
    save_draft(page)
    page.wait_for_function("() => document.querySelector('#save-state').textContent === '已保存'")
    page.wait_for_function("() => !document.querySelector('#secondary-action').hasAttribute('aria-busy')")
    page.locator("#notice").wait_for(state="hidden")
    before = tabs.all_text_contents()
    tabs.nth(0).hover()
    source = tabs.nth(0).bounding_box()
    page.mouse.move(source["x"] + source["width"] / 2, source["y"] + source["height"] / 2)
    page.mouse.down()
    page.mouse.move(source["x"] + 30, source["y"] + 80, steps=8)
    page.locator(".machine-tab.dragging").wait_for()
    page.mouse.move(5, 5, steps=8)
    page.mouse.up()
    assert tabs.all_text_contents() == before
    assert page.locator(".dragging, .drop-target").count() == 0
    assert page.locator("#save-state").text_content() == "已保存"
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.parametrize("selected,removed", [(2, 0), (0, 2), (1, 1), (0, 0), (2, 2)])
def test_machine_tab_delete_preserves_selection(browser_page, ui_server, selected, removed):
    page, server = browser_page, ui_server
    ready(page, server)
    load_existing(page, server)
    for _ in range(2):
        page.get_by_role("button", name="添加机器", exact=True).click()
    tabs = page.locator(".machine-picker .chip-button")
    names = tabs.all_text_contents()
    tabs.nth(selected).click()
    page.locator(f"#input-machines-{selected}-ssh-host").fill("selected-host")
    dialogs = []
    page.on("dialog", lambda dialog: dialogs.append(dialog.type))
    corner = page.get_by_role("button", name=f"删除机器 {names[removed]}", exact=True)
    page.locator(".machine-tab").nth(removed).hover()
    assert corner.evaluate("e => getComputedStyle(e).opacity") == "1"
    corner.click()
    remaining = names[:removed] + names[removed + 1 :]
    expected = names[selected] if selected != removed else remaining[max(0, removed - 1)]
    assert tabs.all_text_contents() == remaining
    assert page.locator(".machine-picker .active").text_content() == expected
    if selected != removed:
        index = remaining.index(expected)
        assert page.locator(f"#input-machines-{index}-ssh-host").input_value() == "selected-host"
    assert dialogs == []
    save_draft(page)
    page.wait_for_function("() => document.querySelector('#save-state').textContent === '已保存'")
    saved = yaml.safe_load(server.existing.read_text())
    assert [m["id"] for m in saved["machines"]] == remaining
    page.reload()
    page.wait_for_function(
        "() => document.querySelectorAll('.machine-picker .chip-button').length === 2"
    )
    assert tabs.all_text_contents() == remaining
    assert page.locator(".machine-picker .active").text_content() == expected


@pytest.mark.parametrize("width", [1440, 390])
def test_machine_tab_inline_keyboard_delete_and_empty_state(browser_page, ui_server, width):
    page, server = browser_page, ui_server
    page.set_viewport_size({"width": width, "height": 1000})
    ready(page, server)
    page.get_by_role("button", name="添加机器", exact=True).click()
    corner = page.get_by_role("button", name="删除机器 server01", exact=True)
    page.mouse.move(0, 0)
    first = page.locator(".machine-picker .chip-button").nth(0)
    edit = page.get_by_role("button", name="修改机器标识 server01", exact=True)
    chip_box, edit_box, remove_box = first.bounding_box(), edit.bounding_box(), corner.bounding_box()
    assert chip_box["x"] + chip_box["width"] <= edit_box["x"]
    assert edit_box["x"] + edit_box["width"] <= remove_box["x"]
    assert edit_box["y"] == remove_box["y"]
    assert edit_box["width"] == remove_box["width"]
    assert edit_box["height"] == remove_box["height"]
    corner.hover()
    page.wait_for_function(
        "() => getComputedStyle(document.querySelector('.machine-remove')).backgroundColor === "
        "'rgb(241, 246, 255)'"
    )
    first.focus()
    first.press("Tab")
    assert edit.evaluate("e => e === document.activeElement")
    page.keyboard.press("Tab")
    assert corner.evaluate("e => e === document.activeElement")
    corner.press("Enter")
    assert page.locator(".machine-picker .active").text_content() == "server02"
    page.get_by_role("button", name="删除机器 server02", exact=True).press("Space")
    assert page.locator(".machine-picker .chip-button").count() == 0
    assert page.get_by_text("添加第一台机器", exact=True).is_visible()
    assert page.get_by_role("button", name="添加机器", exact=True).evaluate(
        "e => e === document.activeElement"
    )
    assert page.locator("#wizard-step-1").is_disabled()
    click_primary(page)
    assert page.locator("#wizard-step-0").get_attribute("aria-current") == "step"
    assert page.get_by_role("button", name="机器：请添加至少一台机器。", exact=True).is_visible()
    page.get_by_role("button", name="添加机器", exact=True).click()
    assert page.locator(".machine-picker .active").text_content() == "server01"
    page.get_by_role("button", name="添加机器", exact=True).click()
    set_editor_mode(page, "yaml")
    result = yaml.safe_load(page.locator("#yaml-editor").input_value())
    assert [m["id"] for m in result["machines"]] == ["server01", "server02"]
    assert all(m["systems"] == [] for m in result["machines"])


def test_machine_tab_renames_only_target_and_preserves_selection(browser_page, ui_server):
    page, server = browser_page, ui_server
    ready(page, server)
    load_existing(page, server)
    page.get_by_role("button", name="添加机器", exact=True).click()
    tabs = page.locator(".machine-picker .chip-button")
    original_width = page.locator(".machine-tab").first.bounding_box()["width"]
    edit = page.get_by_role("button", name="修改机器标识 server01", exact=True)
    page.mouse.move(0, 0)
    assert edit.evaluate("e => getComputedStyle(e).opacity") == "1"
    edit.click()
    dialog = page.get_by_role("dialog", name="修改机器标识")
    value = dialog.get_by_label("机器标识", exact=True)
    assert value.input_value() == "server01"
    assert value.evaluate("e => e === document.activeElement && e.selectionEnd === e.value.length")
    value.fill("server02")
    value.press("Enter")
    assert value.evaluate("e => e.validity.customError")
    value.fill("无效标识")
    value.press("Enter")
    assert value.evaluate("e => e.validity.patternMismatch")
    value.fill("discarded")
    value.press("Escape")
    assert not page.locator("#id-dialog").evaluate("el => el.open")
    assert tabs.all_text_contents() == ["server01", "server02"]
    tabs.first.focus()
    page.keyboard.press("Tab")
    assert edit.evaluate("e => e === document.activeElement")
    edit.press("Enter")
    value.fill("bench-host_01")
    value.press("Enter")
    assert not page.locator("#id-dialog").evaluate("el => el.open")
    assert tabs.all_text_contents() == ["bench-host_01", "server02"]
    assert page.locator(".machine-tab").first.bounding_box()["width"] > original_width
    assert page.locator(".machine-picker .active").text_content() == "server02"
    assert page.get_by_role("button", name="修改机器标识 bench-host_01").evaluate(
        "e => e === document.activeElement"
    )
    save_draft(page)
    page.wait_for_function("() => document.querySelector('#save-state').textContent === '已保存'")
    original = server.config.machines[0].model_dump(mode="json")
    saved = yaml.safe_load(server.existing.read_text())
    assert saved["machines"][0] == {**original, "id": "bench-host_01"}
    page.reload()
    tabs.first.wait_for()
    assert tabs.all_text_contents() == ["bench-host_01", "server02"]


def test_system_step_only_selects_existing_machines(browser_page, ui_server):
    page, server = browser_page, ui_server
    ready(page, server)
    load_existing(page, server)
    for _ in range(2):
        page.get_by_role("button", name="添加机器", exact=True).click()
    for index in (1, 2):
        page.locator(".machine-picker .chip-button").nth(index).click()
        page.locator(f"#input-machines-{index}-ssh-host").fill("localhost")
        page.locator(f"#input-machines-{index}-ssh-username").fill("bench")
    tabs = page.locator(".machine-picker .chip-button")
    tabs.nth(2).press("Alt+ArrowLeft")
    save_draft(page)
    page.wait_for_function("() => document.querySelector('#save-state').textContent === '已保存'")
    original = server.existing.read_text()
    database_icon = tabs.first.locator(".icon").first
    icon_size = "icon => [icon.clientWidth, icon.clientHeight]"
    connection_size = database_icon.evaluate(icon_size)
    machine_tiles = page.locator(".machine-picker .machine-tab")
    tile_frames = """tiles => tiles.map(tile => {
        const { width, height } = tile.getBoundingClientRect();
        const { borderRadius, borderWidth } = getComputedStyle(tile);
        return [width, height, borderRadius, borderWidth];
    })"""
    connection_frames = machine_tiles.evaluate_all(tile_frames)
    open_step(page, "选择系统")
    assert database_icon.evaluate(icon_size) == connection_size
    for system_frame, connection_frame in zip(
        machine_tiles.evaluate_all(tile_frames), connection_frames, strict=True
    ):
        assert system_frame[0] < connection_frame[0]
        assert system_frame[1:] == connection_frame[1:]
    assert tabs.all_text_contents() == ["server01", "server03", "server02"]
    assert page.get_by_role("button", name="添加机器", exact=True).count() == 0
    assert page.get_by_role("button", name="上移机器", exact=True).count() == 0
    assert page.get_by_role("button", name="下移机器", exact=True).count() == 0
    assert page.get_by_role("button", name="移除此机器", exact=True).count() == 0
    assert page.locator(".machine-picker :is(.object-tab-action, [draggable=true])").count() == 0
    tabs.nth(0).hover()
    tabs.nth(0).click()
    assert page.locator(".machine-picker .active").text_content() == "server01"
    assert page.locator(".system-item").count() == 2
    tabs.nth(0).press("Alt+ArrowRight")
    tabs.nth(2).press("Alt+ArrowLeft")
    tabs.nth(0).drag_to(tabs.nth(2))
    assert tabs.all_text_contents() == ["server01", "server03", "server02"]
    assert page.locator("#save-state").text_content() == "已保存"
    tabs.nth(2).click()
    assert page.locator(".machine-picker .active").text_content() == "server02"
    assert page.locator(".system-item").count() == 0
    assert server.existing.read_text() == original
    open_step(page, "连接机器")
    assert page.get_by_role("button", name="添加机器", exact=True).is_visible()
    assert page.locator(".machine-remove").count() == 3


def load_benchmark_picker(page, server, count=3):
    ready(page, server)
    load_existing(page, server)
    set_editor_mode(page, "yaml")
    editor = page.locator("#yaml-editor")
    document = yaml.safe_load(editor.input_value())
    items = [
        {
            "id": f"unixbench{i:02}",
            "type": "unixbench",
            "source_archive": f"archive-{i}.tar.gz",
            "repetitions": i,
            "make_args": [f"-j{i}"],
            "env": {"CASE": f"case-{i}"},
            "tests": ["dhry2reg"],
        }
        for i in range(1, count + 1)
    ]
    document["benchmarks"] = items
    editor.fill(yaml.safe_dump(document))
    set_editor_mode(page, "form")
    open_step(page, "设置测试")
    return items


def test_benchmark_tab_renames_without_changing_selection(browser_page, ui_server):
    page, server = browser_page, ui_server
    original = load_benchmark_picker(page, server)
    last_tab = page.locator(".benchmark-tab").last
    original_width = last_tab.bounding_box()["width"]
    page.get_by_role("button", name="修改测试标识 unixbench01", exact=True).click()
    dialog = page.get_by_role("dialog", name="修改测试标识")
    value = dialog.get_by_label("测试标识", exact=True)
    value.fill("unixbench-new")
    dialog.get_by_role("button", name="取消", exact=True).click()
    assert page.locator(".benchmark-picker .chip-button").first.text_content() == "unixbench01"
    page.get_by_role("button", name="修改测试标识 unixbench03", exact=True).click()
    value.fill("unixbench-new")
    dialog.get_by_role("button", name="确定", exact=True).click()
    assert last_tab.bounding_box()["width"] > original_width
    assert page.locator(".benchmark-picker .active").text_content() == "unixbench01"
    set_editor_mode(page, "yaml")
    saved = yaml.safe_load(page.locator("#yaml-editor").input_value())
    assert saved["benchmarks"] == [*original[:2], {**original[2], "id": "unixbench-new"}]


@pytest.mark.parametrize("width", [1440, 768, 390])
def test_benchmark_drag_and_keyboard_sort_preserve_config(browser_page, ui_server, width):
    page, server = browser_page, ui_server
    page.set_viewport_size({"width": width, "height": 1000})
    original = load_benchmark_picker(page, server)
    tabs = page.locator(".benchmark-picker .chip-button")
    assert page.get_by_role("button", name="上移测试", exact=True).count() == 0
    assert page.get_by_role("button", name="下移测试", exact=True).count() == 0
    assert page.get_by_role("button", name="移除此测试", exact=True).count() == 0
    tabs.nth(2).click()
    tabs.nth(0).drag_to(tabs.nth(2))
    assert tabs.all_text_contents() == ["unixbench02", "unixbench03", "unixbench01"]
    assert page.locator(".benchmark-picker .active").text_content() == "unixbench03"
    assert page.locator("#input-benchmarks-1-source_archive").input_value() == "archive-3.tar.gz"
    tabs.nth(2).drag_to(tabs.nth(0))
    assert tabs.all_text_contents() == ["unixbench01", "unixbench02", "unixbench03"]
    tabs.nth(2).drag_to(tabs.nth(0))
    tabs.nth(0).press("Alt+ArrowRight")
    assert tabs.all_text_contents() == ["unixbench01", "unixbench03", "unixbench02"]
    assert tabs.nth(1).evaluate("e => e === document.activeElement")
    assert page.locator(".benchmark-picker .active").text_content() == "unixbench03"
    tabs.nth(1).press("Alt+ArrowLeft")
    tabs.nth(0).press("Alt+ArrowLeft")
    assert tabs.all_text_contents() == ["unixbench03", "unixbench01", "unixbench02"]
    assert page.locator(".dragging, .drop-target").count() == 0
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    save_draft(page)
    page.wait_for_function("() => document.querySelector('#save-state').textContent === '已保存'")
    saved = yaml.safe_load(server.existing.read_text())
    assert saved["benchmarks"] == [original[2], original[0], original[1]]
    page.reload()
    tabs.first.wait_for()
    assert tabs.all_text_contents() == ["unixbench03", "unixbench01", "unixbench02"]
    set_editor_mode(page, "yaml")
    assert yaml.safe_load(page.locator("#yaml-editor").input_value()) == saved


@pytest.mark.parametrize("width", [1440, 768, 390])
def test_benchmark_drag_across_rows_and_cancel(browser_page, ui_server, width):
    page, server = browser_page, ui_server
    page.set_viewport_size({"width": width, "height": 1000})
    original = load_benchmark_picker(page, server, count=14)
    tabs = page.locator(".benchmark-picker .chip-button")
    tabs.nth(13).click()
    assert tabs.nth(0).bounding_box()["y"] < tabs.nth(13).bounding_box()["y"]
    tabs.nth(13).drag_to(tabs.nth(0))
    assert tabs.all_text_contents() == [original[-1]["id"], *[b["id"] for b in original[:-1]]]
    assert page.locator(".benchmark-picker .active").text_content() == "unixbench14"
    save_draft(page)
    page.wait_for_function("() => document.querySelector('#save-state').textContent === '已保存'")
    page.wait_for_function("() => !document.querySelector('#secondary-action').hasAttribute('aria-busy')")
    page.locator("#notice").wait_for(state="hidden")
    before = tabs.all_text_contents()
    tabs.nth(0).hover()
    source = tabs.nth(0).bounding_box()
    page.mouse.move(source["x"] + source["width"] / 2, source["y"] + source["height"] / 2)
    page.mouse.down()
    page.mouse.move(source["x"] + 30, source["y"] + 80, steps=8)
    page.locator(".benchmark-tab.dragging").wait_for()
    page.mouse.move(5, 5, steps=8)
    page.mouse.up()
    tabs.nth(0).press("Alt+ArrowLeft")
    tabs.nth(13).press("Alt+ArrowRight")
    tabs.nth(0).drag_to(tabs.nth(0))
    assert tabs.all_text_contents() == before
    assert page.locator(".dragging, .drop-target").count() == 0
    assert page.locator("#save-state").text_content() == "已保存"
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.parametrize("selected,removed", [(2, 0), (0, 2), (1, 1), (0, 0), (2, 2)])
def test_benchmark_tab_delete_preserves_selection(browser_page, ui_server, selected, removed):
    page, server = browser_page, ui_server
    original = load_benchmark_picker(page, server)
    tabs = page.locator(".benchmark-picker .chip-button")
    tabs.nth(selected).click()
    dialogs = []
    page.on("dialog", lambda dialog: dialogs.append(dialog.type))
    page.get_by_role("button", name=f"删除测试 {original[removed]['id']}", exact=True).click()
    remaining = original[:removed] + original[removed + 1 :]
    expected = original[selected] if selected != removed else remaining[max(0, removed - 1)]
    assert tabs.all_text_contents() == [b["id"] for b in remaining]
    assert page.locator(".benchmark-picker .active").text_content() == expected["id"]
    index = remaining.index(expected)
    assert (
        page.locator(f"#input-benchmarks-{index}-source_archive").input_value()
        == expected["source_archive"]
    )
    assert tabs.nth(index).evaluate("e => e === document.activeElement")
    assert dialogs == []
    save_draft(page)
    page.wait_for_function("() => document.querySelector('#save-state').textContent === '已保存'")
    saved = yaml.safe_load(server.existing.read_text())
    assert saved["benchmarks"] == remaining
    page.reload()
    tabs.first.wait_for()
    assert tabs.all_text_contents() == [b["id"] for b in remaining]
    assert page.locator(".benchmark-picker .active").text_content() == expected["id"]


def test_benchmark_tab_inline_keyboard_delete_and_empty_state(browser_page, ui_server):
    page, server = browser_page, ui_server
    load_benchmark_picker(page, server, count=2)
    tabs = page.locator(".benchmark-picker .chip-button")
    tabs.nth(1).click()
    page.mouse.move(0, 0)
    first = tabs.first
    edit = page.get_by_role("button", name="修改测试标识 unixbench01", exact=True)
    remove = page.get_by_role("button", name="删除测试 unixbench01", exact=True)
    chip_box, edit_box, remove_box = first.bounding_box(), edit.bounding_box(), remove.bounding_box()
    assert chip_box["x"] + chip_box["width"] <= edit_box["x"]
    assert edit_box["x"] + edit_box["width"] <= remove_box["x"]
    assert edit_box["y"] == remove_box["y"]
    assert remove.evaluate("e => getComputedStyle(e).opacity") == "1"
    first.press("Tab")
    assert edit.evaluate("e => e === document.activeElement")
    page.keyboard.press("Tab")
    assert remove.evaluate("e => e === document.activeElement")
    remove.press("Enter")
    assert page.locator(".benchmark-picker .active").text_content() == "unixbench02"
    assert page.locator("#input-benchmarks-0-source_archive").input_value() == "archive-2.tar.gz"
    page.get_by_role("button", name="删除测试 unixbench02", exact=True).press("Space")
    assert tabs.count() == 0
    assert page.locator(".benchmark-remove").count() == 0
    assert page.get_by_role("button", name="添加测试", exact=True).evaluate(
        "e => e === document.activeElement"
    )
    assert page.get_by_text("尚未添加测试", exact=True).is_visible()
    click_primary(page)
    assert page.get_by_role("button", name="测试：请添加至少一项测试。", exact=True).is_visible()
    page.get_by_role("button", name="添加测试", exact=True).click()
    assert page.locator("#input-benchmarks-0-source_archive").input_value() == ""
    assert page.locator(".benchmark-picker .active").text_content() == "unixbench01"


@pytest.mark.parametrize("field,invalid", [("policy-poll_interval", "-1"), ("version", "2")])
def test_run_settings_error_navigation_and_validation_invalidation(
    browser_page, ui_server, field, invalid
):
    page, server = browser_page, ui_server
    ready(page, server)
    load_existing(page, server)
    open_step(page, "检查运行")
    click_primary(page)
    page.wait_for_function(
        "() => document.querySelector('#primary-action').textContent.includes('开始测试')"
    )
    open_step(page, "运行设置")
    assert page.locator("#primary-action").text_content() == "下一步"
    page.get_by_text("高级调度设置", exact=True).click()
    control = page.locator("#input-" + field)
    original = control.input_value()
    control.fill(invalid)
    assert page.locator("#validation-result").is_hidden()
    click_primary(page)
    assert page.locator("#primary-action").text_content() == "保存并检查"
    click_primary(page)
    page.locator("#config-errors").wait_for()
    page.locator(".error-link").first.click()
    assert page.locator(".wizard-step[aria-current=step]").text_content() == "4运行设置"
    assert control.is_visible()
    assert control.get_attribute("aria-invalid") == "true"
    assert control.evaluate("e => e === document.activeElement")
    control.fill(original)
    click_primary(page)
    click_primary(page)
    page.wait_for_function(
        "() => document.querySelector('#primary-action').textContent.includes('开始测试')"
    )
    assert not server.world.starts


def test_imported_yaml_enters_guided_steps_without_changing_content(browser_page, ui_server):
    page, server = browser_page, ui_server
    ready(page, server, startup_path=server.root / "startup.yaml")
    raw = server.config.model_dump(mode="json")
    raw["machines"][0]["systems"][0]["ssh"] = {"password": None, "client_keys": [], "proxy": None}
    text = "# imported config\n" + yaml.safe_dump(raw)
    page.locator("#file-menu summary").click()
    page.locator("#import").set_input_files(
        {"name": "import.yaml", "mimeType": "text/yaml", "buffer": text.encode()}
    )
    page.get_by_text("已导入 YAML 内容，请确认服务器上的保存位置。", exact=True).wait_for()
    assert page.locator(".wizard-step[aria-current=step]").text_content() == "1连接机器"
    open_step(page, "运行设置")
    set_editor_mode(page, "yaml")
    page.locator("#yaml-panel").wait_for()
    assert page.locator("#yaml-editor").input_value() == text
    set_editor_mode(page, "form")
    assert page.locator(".wizard-step[aria-current=step]").text_content() == "4运行设置"
    save_draft(page)
    page.locator("#save-path").fill(str(server.root / "autobench.yaml"))
    page.locator("#path-confirm").click()
    page.wait_for_function("() => document.querySelector('#save-state').textContent === '已保存'")
    assert (server.root / "autobench.yaml").read_text() == text


@pytest.mark.parametrize("width", [1440, 390, 320])
def test_guided_system_order_delete_and_readd(browser_page, ui_server, width):
    page, server = browser_page, ui_server
    page.set_viewport_size({"width": width, "height": 1000})
    ready(page, server)
    load_existing(page, server)
    open_step(page, "选择系统")
    second_row = page.locator(".system-item").nth(1)
    actions = second_row.locator(".system-row-actions button")
    assert actions.evaluate_all("buttons => buttons.map(b => b.getAttribute('aria-label'))") == [
        "编辑系统配置 os2", "删除系统 os2", "上移系统 os2", "下移系统 os2"
    ]
    assert actions.evaluate_all("""buttons => buttons.every((button, index) =>
        index === 0 || buttons[index - 1].getBoundingClientRect().right <= button.getBoundingClientRect().left
    )""")
    second_row.get_by_role("button", name="编辑系统配置 os2", exact=True).click()
    assert page.get_by_label("系统标识 *", exact=True).input_value() == "os2"
    page.get_by_role("button", name="返回系统列表", exact=True).click()
    page.get_by_role("button", name="下移系统 os1", exact=True).click()
    assert page.locator(".system-item .object-title").all_text_contents() == ["os2", "os1"]
    page.get_by_role("button", name="上移系统 os1", exact=True).click()
    assert page.locator(".system-item .object-title").all_text_contents() == ["os1", "os2"]
    for _ in range(2):
        item = page.locator(".system-item").first
        item.get_by_role("button", name="删除系统", exact=False).click()
    assert page.get_by_role("heading", name="尚未添加系统", exact=True).is_visible()
    page.get_by_role("button", name="手动添加系统", exact=True).click()
    editor = page.locator(".system-editor")
    assert editor.get_by_role("heading").all_text_contents() == ["基本信息", "配置选项", "环境变量"]
    editor.get_by_role("button", name="添加变量", exact=True).click()
    editor.get_by_role("textbox", name="NEW_VAR的值", exact=True).fill("value")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.get_by_role("button", name="返回系统列表", exact=True).click()
    assert page.get_by_text("待填写启动项", exact=True).is_visible()
    page.locator(".systems-board").get_by_role("button", name="linux01", exact=True).click()
    page.locator("#input-machines-0-systems-0-boot_entry").fill("00AB")
    page.get_by_role("button", name="返回系统列表", exact=True).click()
    assert page.locator(".system-item .object-title").all_text_contents() == ["linux01"]
    assert page.get_by_text("Boot00AB", exact=True).is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.get_by_role("button", name="编辑系统配置 linux01", exact=True).click()
    page.get_by_label("系统标识 *", exact=True).fill("linux-new")
    page.get_by_role("button", name="返回系统列表", exact=True).click()
    assert page.locator(".system-item .object-title").all_text_contents() == ["linux-new"]
    set_editor_mode(page, "yaml")
    page.locator("#yaml-panel").wait_for()
    data = yaml.safe_load(page.locator("#yaml-editor").input_value())
    assert data["machines"][0]["systems"] == [
        {"id": "linux-new", "boot_entry": "00AB", "env": {"NEW_VAR": "value"}}
    ]


def report_history(server, *, empty=False, parallel_copies=64, extra_copies=()):
    from test_reports import TEST_ORDER, unixbench_text, write_round

    from autobench.storage import Store

    config = server.config.model_copy(deep=True)
    config.benchmarks[0].repetitions = 3
    with_store = Store(config.policy.state_dir)
    run_id = with_store.create(config)
    if not empty:
        root = config.policy.output_dir / run_id
        for system, values in (("os1", (100, 200, 900)), ("os2", (480, 480, 480))):
            for repetition, value in enumerate(values, 1):
                text = ""
                for copies in (1, parallel_copies, *extra_copies):
                    extra = "".join(
                        f"{name}   1.0   10000.0   {value * copies}\n" for name in TEST_ORDER[2:]
                    )
                    text += unixbench_text(value * copies, value * copies / 10, copies, extra=extra)
                write_round(root, system, repetition, text, machine="server01")
        for job in with_store.jobs(run_id):
            with_store.update(job["id"], "succeeded", archived=True)
        with_store.set_run_status(run_id, "succeeded")
    with_store.close()
    return run_id


def open_report_history(page, server, run_id):
    page.add_init_script(
        "sessionStorage.setItem('autobench-selection', "
        + json.dumps(json.dumps({"id": run_id, "stateDir": str(server.config.policy.state_dir)}))
        + "); sessionStorage.setItem('autobench-page', 'runs');"
    )
    ready(page, server)
    page.locator("#tab-runs").click()
    page.locator("#generate-report").wait_for()


@pytest.mark.parametrize("width", [1280, 1440, 1920, 390, 320])
def test_report_combines_scores_and_round_counts_for_each_group(browser_page, ui_server, width):
    from test_reports import write_round

    page, server = browser_page, ui_server
    extra = server.config.machines[0].systems[0].model_copy(deep=True)
    extra.id = "system_" + "longname_" * 6
    extra.boot_entry = "0003"
    server.config.machines[0].systems.append(extra)
    run_id = report_history(server, parallel_copies=8, extra_copies=(128, 256))
    write_round(
        server.config.policy.output_dir / run_id, extra.id, 1, "invalid", machine="server01"
    )
    page.set_viewport_size({"width": width, "height": 900})
    open_report_history(page, server, run_id)
    page.locator("#generate-report").click()
    page.locator("#download-report").wait_for()
    summary = page.get_by_role("region", name="比较组对比表，可横向滚动")
    assert summary.get_by_role("columnheader").all_text_contents() == [
        "比较组",
        "1 parallel copy",
        "8 parallel copies",
        "128 parallel copies",
        "计划轮次",
        "已回收",
        "已解析",
        "256 parallel copies",
    ]
    assert page.get_by_role("heading", name="轮次概况", exact=True).count() == 0
    rows = summary.locator("tbody").get_by_role("row")
    assert rows.count() == 3
    assert rows.first.get_by_role("cell").all_text_contents()[1:] == [
        "40.00 (0.00%)",
        "320.00 (0.00%)",
        "5120.00 (0.00%)有效轮次 3/3",
        "3",
        "3",
        "3",
        "10240.00 (0.00%)有效轮次 3/3",
    ]
    cells = rows.last.get_by_role("cell")
    assert extra.id in cells.first.text_content()
    assert cells.all_text_contents()[4:7] == ["3", "1", "0"]
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    if width <= 390:
        assert summary.evaluate("el => el.scrollWidth > el.clientWidth")
        summary.press("ArrowRight")
        page.wait_for_function("el => el.scrollLeft > 0", arg=summary.element_handle())
    if output := os.getenv("AUTOBENCH_UI_SCREENSHOTS"):
        summary.screenshot(path=Path(output) / f"unixbench-total-comparison-{width}.png")


@pytest.mark.parametrize(
    ("width", "text_size"),
    [(1280, 100), (1440, 100), (1920, 100), (390, 100), (320, 100), (1440, 200), (390, 200)],
)
def test_report_copies_menu_displays_full_labels(browser_page, ui_server, width, text_size):
    from playwright.sync_api import expect

    page, server = browser_page, ui_server
    page.set_viewport_size({"width": width, "height": 1000})
    page.emulate_media(reduced_motion="reduce")
    run_id = report_history(server, parallel_copies=128)
    if text_size == 200:

        def apply_text_zoom(route):
            response = route.fetch()
            route.fulfill(response=response, body=response.text() + "\nhtml { font-size: 200%; }")

        page.route("**/static/style.css", apply_text_zoom)
    open_report_history(page, server, run_id)
    page.get_by_role("button", name="生成报告", exact=True).click()
    copies = page.get_by_role("combobox", name="并发数", exact=True)
    expect(copies).to_have_css("font-size", f"{14 * text_size / 100:g}px")
    summary = page.get_by_role("region", name="比较组对比表，可横向滚动")
    assert summary.get_by_role("columnheader").all_text_contents()[-4:] == [
        "128 parallel copies",
        "计划轮次",
        "已回收",
        "已解析",
    ]
    assert summary.evaluate("""el => {
        el.scrollLeft = el.scrollWidth;
        const headers = el.querySelectorAll('th');
        const first = headers[0].getBoundingClientRect();
        const last = headers[headers.length - 1].getBoundingClientRect();
        return last.left >= first.right - 1 && last.right <= el.getBoundingClientRect().right + 1;
    }""")
    native_picker = page.evaluate("""CSS.supports("appearance: base-select") &&
        CSS.supports("selector(select::picker(select))")""")
    if native_picker:
        options = copies.locator("option")
    else:
        options = page.get_by_role("listbox", name="并发数", exact=True).get_by_role("option")

    detail = page.get_by_role("region", name="测试项对比表，可横向滚动")
    expect(detail).to_contain_text("400.00 (0.00%)")
    expect(detail).not_to_contain_text("有效轮次")
    copies.click()
    expect(options.last).to_be_visible()
    assert options.all_text_contents() == ["1 parallel copy", "128 parallel copies"]
    assert options.evaluate_all("""options => options.every(option => {
        const bounds = option.getBoundingClientRect();
        const label = new Range();
        label.selectNodeContents(option);
        return option.scrollWidth <= option.clientWidth &&
            [...label.getClientRects()].every(rect =>
                rect.left >= bounds.left && rect.right <= bounds.right &&
                rect.right <= document.documentElement.clientWidth);
    })""")
    options.last.click()
    expect(copies).to_have_value("128")
    expect(detail).to_contain_text("51200.00 (0.00%)")
    expect(detail).not_to_contain_text("有效轮次")
    copies.press("Space")
    expect(options.first).to_be_visible()
    page.keyboard.press("Escape")
    expect(options.first).to_be_hidden()
    expect(copies).to_be_focused()
    expect(copies).to_have_value("128")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.parametrize(("width", "text_size"), [(1440, 100), (768, 100), (390, 100), (390, 200)])
def test_report_preview_baseline_download_and_layout(browser_page, ui_server, width, text_size):
    from openpyxl import load_workbook
    from playwright.sync_api import expect

    page, server = browser_page, ui_server
    run_id = report_history(server)
    page.set_viewport_size({"width": width, "height": 900})
    if text_size == 200:

        def apply_text_zoom(route):
            response = route.fetch()
            route.fulfill(response=response, body=response.text() + "\nhtml { font-size: 200%; }")

        page.route("**/static/style.css", apply_text_zoom)
    calls = []
    page.on("request", lambda req: calls.append(req.url) if "/report?" in req.url else None)
    open_report_history(page, server, run_id)
    assert not calls
    page.locator("#generate-report").click()
    page.locator("#download-report").wait_for()
    assert len(calls) == 1
    panel = page.locator(".report-panel")
    assert page.get_by_role("region", name="比较组对比表，可横向滚动").get_by_role(
        "columnheader"
    ).all_text_contents() == [
        "比较组",
        "1 parallel copy",
        "64 parallel copies",
        "计划轮次",
        "已回收",
        "已解析",
    ]
    assert panel.locator(".report-table table").evaluate_all("""tables => tables.every(table => {
        const columns = [...table.tHead.rows[0].cells];
        return [...table.tBodies[0].rows].every(row => [...row.cells].every((cell, index) => {
            const heading = columns[index].getBoundingClientRect();
            const data = cell.getBoundingClientRect();
            return Math.abs(heading.left - data.left) <= 1 &&
                Math.abs(heading.right - data.right) <= 1;
        }));
    })""")
    assert "400.00 (0.00%)" in panel.text_content()
    assert "480.00 (+20.00%)" in panel.text_content()
    assert "有效轮次 3/3" in panel.text_content()
    page.locator("#report-copies").select_option("64")
    assert "25600.00 (0.00%)" in panel.text_content()
    # Polling must keep the report DOM, selection, and keyboard focus intact.
    page.locator("#report-copies").focus()
    page.wait_for_timeout(2300)
    assert len(calls) == 1
    assert page.locator("#report-copies").input_value() == "64"
    assert page.evaluate("document.activeElement.id") == "report-copies"
    baseline = page.get_by_role("combobox", name="比较基线", exact=True)
    baseline.focus()
    page.evaluate("""() => {
        const selectors = [
            '.report-panel .section-heading', '#report-baseline', '#report-copies',
            '#download-report', '.report-overall .report-table', '.report-panel > .report-table',
        ];
        window.reportLayout = selectors.map(selector => {
            const element = document.querySelector(selector);
            if (element.matches('.report-table')) element.scrollLeft = 36;
            return { element, top: element.getBoundingClientRect().top, left: element.scrollLeft };
        });
        window.reportLayoutStable = () => window.reportLayout.every(({element, top, left}) =>
            element.isConnected && Math.abs(element.getBoundingClientRect().top - top) <= 1
                && element.scrollLeft === left
        );
    }""")
    pending = []
    page.route("**/report?*baseline=*", lambda route: pending.append(route))
    with page.expect_request("**/report?*baseline=*"):
        baseline.select_option("server01/os2/ub")
    expect(panel).to_have_attribute("aria-busy", "true")
    expect(baseline).to_be_focused()
    download_link = page.locator("#download-report")
    expect(download_link).to_be_visible()
    expect(download_link).to_be_disabled()
    assert download_link.get_attribute("href") is None
    assert page.evaluate("window.reportLayoutStable()")
    expect(panel).to_contain_text("25600.00 (0.00%)")
    pending.pop().continue_()
    page.unroute("**/report?*baseline=*")
    expect(panel).to_have_attribute("aria-busy", "false")
    expect(panel).to_contain_text("25600.00 (-16.67%)")
    expect(baseline).to_be_focused()
    assert page.evaluate("window.reportLayoutStable()")
    with page.expect_download() as download:
        page.locator("#download-report").click()
    result = download.value
    assert result.suggested_filename == f"unixbench-{run_id}.xlsx"
    with result.path().open("rb") as stream:
        book = load_workbook(stream)
    assert book["1 parallel copy"]["B4"].value == "400.00 (-16.67%)"
    assert book["64 parallel copies"]["B4"].value == "25600.00 (-16.67%)"
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert (
        panel.locator(".report-table").first.evaluate(
            "el => getComputedStyle(el.querySelector('td')).position"
        )
        == "sticky"
    )
    page.locator("#generate-report").click()
    expect(panel).to_have_attribute("aria-busy", "false")
    assert page.locator("#report-baseline").input_value() == "server01/os2/ub"


def test_report_loading_error_retry_and_empty_state(browser_page, ui_server):
    page, server = browser_page, ui_server
    run_id = report_history(server, empty=True)
    pending = []
    page.route("**/report?*", lambda route: pending.append(route))
    open_report_history(page, server, run_id)
    page.locator("#generate-report").click()
    page.wait_for_function(
        "() => document.querySelector('.report-panel').getAttribute('aria-busy') === 'true'"
    )
    assert page.locator("#generate-report").is_disabled()
    assert page.locator(".report-panel").get_by_role("status").is_visible()
    assert len(pending) == 1
    pending.pop().fulfill(status=500, json={"detail": "模拟读取失败"})
    page.locator(".report-panel").get_by_role("alert").wait_for()
    page.unroute("**/report?*")
    page.get_by_role("button", name="重试", exact=True).click()
    page.get_by_text("暂无可解析的 INDEX 结果。结果回收后可刷新报告。", exact=True).wait_for()
    summary = page.get_by_role("region", name="比较组对比表，可横向滚动")
    assert summary.get_by_role("columnheader").all_text_contents() == [
        "比较组",
        "计划轮次",
        "已回收",
        "已解析",
    ]
    assert summary.get_by_role("row").nth(1).get_by_role("cell").all_text_contents()[1:] == [
        "3",
        "0",
        "0",
    ]
    assert page.locator(".report-warnings").get_attribute("open") is not None


def test_report_stale_baseline_response_cannot_replace_new_selection(browser_page, ui_server):
    from playwright.sync_api import expect

    page, server = browser_page, ui_server
    run_id = report_history(server)
    open_report_history(page, server, run_id)
    page.locator("#generate-report").click()
    page.locator("#download-report").wait_for()
    pending = []
    page.route("**/report?*baseline=*", lambda route: pending.append(route))
    page.locator("#report-baseline").select_option("server01/os2/ub")
    page.locator("#report-baseline").select_option("server01/os1/ub")
    page.wait_for_timeout(50)
    assert len(pending) == 2
    pending[1].continue_()
    expect(page.locator(".report-panel")).to_have_attribute("aria-busy", "false")
    pending[0].continue_()
    page.wait_for_timeout(150)
    assert page.locator("#report-baseline").input_value() == "server01/os1/ub"
    assert "480.00 (+20.00%)" in page.locator(".report-panel").text_content()


def test_report_stale_response_does_not_leak_into_another_run(browser_page, ui_server):
    page, server = browser_page, ui_server
    first = report_history(server)
    second = report_history(server, empty=True)
    open_report_history(page, server, first)
    pending = []
    page.route("**/report?*", lambda route: pending.append(route))
    page.locator("#generate-report").click()
    page.wait_for_function(
        "() => document.querySelector('.report-panel').getAttribute('aria-busy') === 'true'"
    )
    page.locator("#back-to-runs").click()
    page.locator("#runs-list tbody tr").filter(has_text=second).get_by_role(
        "button", name="查看详情", exact=True
    ).click()
    page.wait_for_function(
        "id => document.querySelector('#run-detail').textContent.includes(id)", arg=second
    )
    pending.pop().continue_()
    page.wait_for_timeout(150)
    assert page.locator("#generate-report").text_content() == "生成报告"
    assert "400.00" not in page.locator(".report-panel").text_content()


@pytest.mark.parametrize("mode", ["reflow", "text-zoom", "text-spacing"])
def test_accessible_workbench_layout(browser_page, ui_server, mode):
    from playwright.sync_api import expect

    page, server = browser_page, ui_server
    page.set_viewport_size({"width": 320 if mode == "reflow" else 1440, "height": 900})
    page.emulate_media(reduced_motion="reduce")
    override = ""
    if mode == "text-zoom":
        override = "html { font-size: 200% !important; }"
    if mode == "text-spacing":
        override = """
          * { line-height: 1.5 !important; letter-spacing: .12em !important;
              word-spacing: .16em !important; }
          p { margin-bottom: 2em !important; }
        """
    if override:
        def apply_user_styles(route):
            response = route.fetch()
            route.fulfill(response=response, body=response.text() + "\n" + override)

        page.route("**/static/style.css", apply_user_styles)
    ready(page, server, startup_path=server.existing)
    scale = 2 if mode == "text-zoom" else 1
    host = page.locator("#input-machines-0-ssh-host")
    expect(host).to_have_css("font-size", f"{14 * scale}px")
    auth = page.get_by_role("combobox", name="认证方式", exact=True)
    expect(auth).to_have_css("font-size", f"{14 * scale}px")
    for step in ["连接机器", "选择系统", "设置测试", "运行设置", "检查运行"]:
        open_step(page, step)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), step
    expect(page.locator(".review-panel h2")).to_have_css("font-size", f"{20 * scale}px")
    expect(page.locator(".review-panel .stat strong").first).to_have_css(
        "font-size", f"{32 * scale}px"
    )
    page.get_by_role("tab", name="运行记录").click()
    page.get_by_role("button", name="更改位置…").click()
    confirm = page.get_by_role("button", name="选择目录", exact=True)
    expect(confirm).to_be_enabled()
    box = page.locator("#file-dialog").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= page.viewport_size["width"]
    page.keyboard.press("Escape")
    expect(page.get_by_role("button", name="更改位置…")).to_be_focused()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_file_picker_keeps_keyboard_selection_and_restores_focus(browser_page, ui_server):
    from playwright.sync_api import expect

    page = browser_page
    ready(page, ui_server, startup_path=ui_server.existing)
    page.locator("#file-menu summary").click()
    page.get_by_role("button", name="打开服务器配置…").press("Enter")
    entry = page.get_by_role("button", name="existing.yaml 文件", exact=True)
    entry.press("Space")
    expect(entry).to_be_focused()
    expect(entry).to_have_attribute("aria-pressed", "true")
    expect(page.get_by_role("button", name="选择文件", exact=True)).to_be_enabled()
    page.keyboard.press("Escape")
    expect(page.get_by_role("button", name="文件操作")).to_be_focused()


def test_runs_retry_retains_last_known_list(browser_page, ui_server):
    from playwright.sync_api import expect

    page, server = browser_page, ui_server
    run_id = report_history(server)
    ready(page, server, startup_path=server.existing)
    page.get_by_role("tab", name="运行记录").click()
    page.get_by_text(run_id, exact=True).wait_for()
    page.route("**/api/runs?*", lambda route: route.fulfill(
        status=503, json={"detail": "服务暂时不可用"}
    ))
    expect(page.get_by_role("status").filter(has_text="数据可能已过期")).to_be_visible()
    expect(page.get_by_text(run_id, exact=True)).to_be_visible()
    page.unroute("**/api/runs?*")
    expect(page.get_by_role("status").filter(has_text="数据可能已过期")).to_be_hidden()


@pytest.mark.skipif(
    not os.getenv("AUTOBENCH_UI_SCREENSHOTS"),
    reason="set AUTOBENCH_UI_SCREENSHOTS to capture Chromium reference screenshots",
)
def test_capture_workbench_reference_screens(browser_page, ui_server):
    from playwright.sync_api import expect

    page, server = browser_page, ui_server
    output = Path(os.environ["AUTOBENCH_UI_SCREENSHOTS"])
    output.mkdir(parents=True, exist_ok=True)
    page.emulate_media(reduced_motion="reduce")
    page.set_viewport_size({"width": 1440, "height": 900})
    run_id = report_history(server)
    ready(page, server, startup_path=server.existing)

    def capture(name):
        page.screenshot(path=output / f"{name}.png", animations="disabled", full_page=True)

    capture("01-connection")
    page.get_by_role("combobox", name="认证方式", exact=True).press("Space")
    page.get_by_role("option", name="密钥认证", exact=True).wait_for()
    capture("02-select-focus")
    page.keyboard.press("Escape")
    open_step(page, "选择系统")
    capture("03-systems")
    page.get_by_role("button", name="编辑系统配置 os1", exact=True).click()
    capture("04-system")
    open_step(page, "设置测试")
    capture("05-benchmarks")
    open_step(page, "运行设置")
    capture("06-settings")
    open_step(page, "检查运行")
    capture("07-review")
    page.get_by_role("tab", name="运行记录").click()
    page.get_by_text(run_id, exact=True).wait_for()
    capture("08-history")
    page.get_by_role("button", name="查看详情", exact=True).first.click()
    expect(page.get_by_role("heading", name="运行详情", exact=True)).to_be_visible()
    capture("09-run")
    page.locator("#back-to-runs").click()
    page.get_by_role("button", name="更改位置…").click()
    expect(page.get_by_role("button", name="选择目录", exact=True)).to_be_enabled()
    capture("10-picker")
    page.route("**/api/files?*", lambda route: route.fulfill(
        status=403, json={"detail": "无权读取此目录"}
    ))
    page.get_by_role("button", name="前往", exact=True).click()
    page.get_by_role("alert").filter(has_text="无权读取此目录").wait_for()
    capture("11-picker-error")
    page.keyboard.press("Escape")
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("tab", name="测试配置").click()
    open_step(page, "连接机器")
    capture("12-connection-mobile")


def test_forced_colors_keeps_keyboard_focus_visible(browser_page, ui_server):
    from playwright.sync_api import expect

    page = browser_page
    page.emulate_media(forced_colors="active", reduced_motion="reduce")
    ready(page, ui_server, startup_path=ui_server.existing)
    host = page.locator("#input-machines-0-ssh-host")
    host.focus()
    expect(host).to_have_css("outline-style", "solid")
    expect(host).to_have_css("outline-width", "2px")
    page.keyboard.press("Tab")
    port = page.get_by_role("spinbutton", name="端口")
    expect(port).to_be_focused()
    expect(port).to_have_css("outline-style", "solid")
    for selector in ["body", ".topbar", ".brand-mark", ".plan-heading", "#primary-action"]:
        expect(page.locator(selector)).to_have_css("background-image", "none")
    auth = page.get_by_role("combobox", name="认证方式", exact=True)
    auth.press("Space")
    expect(auth.locator("option:checked")).to_have_css("outline-style", "solid")
    page.keyboard.press("Escape")
    expect(auth).to_be_focused()


@pytest.mark.parametrize("reduced_motion", ["no-preference", "reduce"])
def test_motion_keeps_step_disclosure_and_switch_state_immediate(
    browser_page, ui_server, reduced_motion
):
    from playwright.sync_api import expect

    page = browser_page
    page.emulate_media(reduced_motion=reduced_motion)
    ready(page, ui_server, startup_path=ui_server.existing)
    duration = "0s" if reduced_motion == "reduce" else "0.16s"

    settings = page.get_by_role("button", name="运行设置")
    settings.evaluate("el => window.originalStep = el")
    open_step(page, "设置测试")
    warmup = page.get_by_role("checkbox", name="预热", exact=True)
    initial_checked = warmup.is_checked()
    warmup.press("Space")
    assert warmup.is_checked() != initial_checked
    warmup.press("Space")
    assert warmup.is_checked() == initial_checked
    assert warmup.evaluate("el => getComputedStyle(el, '::before').transitionDuration") == duration

    settings.press("Space")
    assert settings.evaluate("el => el === window.originalStep")
    expect(settings).to_have_attribute("aria-current", "step")
    step_number = settings.locator(".step-number")
    assert step_number.evaluate("el => getComputedStyle(el).transitionDuration.split(', ')[0]") == (
        duration
    )
    policy = page.get_by_text("高级调度设置", exact=True)
    accordion = page.locator("details").filter(has=policy)
    summary = accordion.locator("summary")
    policy_open = accordion.evaluate("el => el.open")
    if not policy_open:
        summary.press("Space")
    interval = page.get_by_label("轮询间隔（秒）", exact=True)
    interval.fill("4")
    summary.press("Space")
    assert not accordion.evaluate("el => el.open")
    assert interval.evaluate("el => { el.focus(); return document.activeElement !== el; }")
    summary.press("Space")
    assert accordion.evaluate("el => el.open")
    expect(interval).to_have_value("4")
    expect(accordion.locator(".disclosure-chevron")).to_have_css("rotate", "90deg")
    if page.evaluate("CSS.supports('interpolate-size: allow-keywords')"):
        accordion_duration = accordion.evaluate(
            "el => getComputedStyle(el, '::details-content').transitionDuration.split(', ')[0]"
        )
        assert accordion_duration == ("0s" if reduced_motion == "reduce" else "0.2s")
    assert not page.locator("#config-body").evaluate("el => el.getAnimations().length")


@pytest.mark.parametrize(
    "width,motion", [(1440, "no-preference"), (390, "no-preference"), (390, "reduce")]
)
def test_file_picker_fades_with_backdrop_and_keeps_layout(browser_page, ui_server, width, motion):
    from playwright.sync_api import expect

    page = browser_page
    page.set_viewport_size({"width": width, "height": 720})
    page.emulate_media(reduced_motion=motion)
    ready(page, ui_server, startup_path=ui_server.existing)
    page.get_by_role("tab", name="运行记录").click()
    opener = page.get_by_role("button", name="更改位置…", exact=True)
    opener.focus()

    def sample_fade(trigger):
        return trigger.evaluate(
            """button => {
                const dialog = document.getElementById('file-dialog');
                button.click();
                const fades = document.getAnimations().filter(animation =>
                    animation.effect.target === dialog &&
                    animation.transitionProperty === 'opacity'
                );
                for (const fade of fades) fade.currentTime = fade.effect.getTiming().duration / 2;
                const backdrop = getComputedStyle(dialog, '::backdrop');
                return {
                    opacities: [Number(getComputedStyle(dialog).opacity), Number(backdrop.opacity)],
                    hitTest: backdrop.pointerEvents,
                    bounds: dialog.getBoundingClientRect().toJSON(),
                };
            }"""
        )

    opened = sample_fade(opener)
    expect(page.get_by_role("button", name="选择目录", exact=True)).to_be_enabled()
    dialog = page.locator("#file-dialog")
    expect(dialog).to_have_css("opacity", "1")
    bounds = dialog.bounding_box()
    closed = sample_fade(dialog.get_by_role("button", name="取消", exact=True))

    if motion == "reduce":
        assert opened["opacities"] == [1, 1]
        assert closed["opacities"] == [0, 0]
        assert dialog.is_hidden()
    else:
        for sample in (opened, closed):
            panel, backdrop = sample["opacities"]
            assert 0 < panel < 1
            assert backdrop == pytest.approx(panel, abs=0.02)
        for key, value in bounds.items():
            assert closed["bounds"][key] == pytest.approx(value, abs=1)
        dialog.wait_for(state="hidden")
    assert closed["hitTest"] == "none"
    expect(opener).to_be_focused()


def test_modal_exit_restores_focus_and_allows_immediate_reopen(browser_page, ui_server):
    from playwright.sync_api import expect

    page = browser_page
    ready(page, ui_server, startup_path=ui_server.existing)
    page.get_by_role("tab", name="运行记录").click()
    opener = page.get_by_role("button", name="更改位置…", exact=True)
    opener.click()
    dialog = page.locator("#file-dialog")
    expect(page.get_by_role("button", name="选择目录", exact=True)).to_be_enabled()
    page.keyboard.press("Escape")
    assert not dialog.evaluate("el => el.open")
    expect(opener).to_be_focused()
    assert dialog.evaluate(
        "el => { el.querySelector('input').focus(); return !el.contains(document.activeElement); }"
    )
    point = opener.bounding_box()
    page.mouse.click(point["x"] + point["width"] / 2, point["y"] + point["height"] / 2)
    expect(page.get_by_role("button", name="选择目录", exact=True)).to_be_enabled()
    page.keyboard.press("Escape")
    dialog.wait_for(state="hidden")
    expect(opener).to_be_focused()


def test_run_events_keep_focused_log_and_scroll_position(browser_page, ui_server):
    from playwright.sync_api import expect

    from autobench.storage import Store

    page, server = browser_page, ui_server
    store = Store(server.config.policy.state_dir)
    run_id = store.create(server.config)
    store.close()
    events = [
        {"sequence": i, "time": 1700000000, "message": f"采集测试事件 {i:03d}"}
        for i in range(1, 81)
    ]

    def event_response(route):
        after = int(route.request.url.split("after=")[-1])
        route.fulfill(json=[event for event in events if event["sequence"] > after])

    page.route(f"**/api/runs/{run_id}/events?*", event_response)
    ready(page, server, startup_path=server.existing)
    page.get_by_role("tab", name="运行记录").click()
    page.get_by_role("button", name="查看详情", exact=True).click()
    page.get_by_text("原始运行事件", exact=True).click()
    log = page.get_by_label("运行事件日志")
    expect(log).to_contain_text("采集测试事件 080")
    log.evaluate("el => { el.scrollTop = 0; el.focus({preventScroll: true}); }")
    expect(page.get_by_role("button", name="回到底部", exact=True)).to_be_visible()
    page.locator("#run-detail").evaluate(
        """el => {
            el.addEventListener('animationstart', () => el.dataset.pollAnimation = 'true');
        }"""
    )
    events.append({"sequence": 81, "time": 1700000000, "message": "新事件 081"})
    expect(log).to_contain_text("新事件 081")
    expect(log).to_be_focused()
    assert log.evaluate("el => el.scrollTop") == 0
    assert page.locator("#run-detail").get_attribute("data-poll-animation") is None
    page.get_by_role("button", name="回到底部", exact=True).click()
    expect(log).to_be_focused()
    assert log.evaluate("el => el.scrollHeight - el.scrollTop - el.clientHeight < 24")
