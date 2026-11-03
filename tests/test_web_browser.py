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


def set_editor_mode(page, mode, *, succeeds=True):
    toggle = page.get_by_role("switch", name="YAML 源码模式", exact=True)
    checked = str(mode == "yaml").lower()
    if toggle.get_attribute("aria-checked") != checked:
        toggle.click()
    if not succeeds:
        page.wait_for_function("() => !document.querySelector('#mode-toggle').disabled")
        assert toggle.get_attribute("aria-checked") != checked
        assert page.locator('#notice[data-tone="error"]').is_visible()
        return
    page.wait_for_function(
        "checked => document.querySelector('#mode-toggle').getAttribute('aria-checked') === checked",
        arg=checked,
    )


def open_step(page, name):
    page.locator("#wizard-steps").get_by_role("button", name=name).click()


def open_system(page, name):
    open_step(page, "选择系统")
    page.locator(".systems-board").get_by_role("button", name=name, exact=True).click()


def fill_connection(page, server):
    page.locator("#input-machines-0-ssh-host").fill("localhost")
    page.locator("#input-machines-0-ssh-username").fill("bench")
    page.locator("#input-machines-0-ssh-password").fill("browser-secret")
    page.get_by_text("高级连接设置", exact=True).click()
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
    assert page.locator("main").evaluate("el => el.getBoundingClientRect().height") == original_height
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.clock.run_for(duration - 1)
    assert notice.is_visible()
    page.clock.run_for(1)
    if reduced_motion == "no-preference":
        assert notice.get_attribute("data-phase") == "closing"
        assert notice.text_content() == "操作提示"
        page.clock.run_for(140)
    assert notice.is_hidden()
    assert notice.text_content() == ""
    assert notice.bounding_box() is None
    assert page.locator(".project-bar").bounding_box() == original_position
    assert page.locator("main").evaluate("el => el.getBoundingClientRect().height") == original_height


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
    page.clock.run_for(140)
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
                transform: getComputedStyle(notice).transform,
            };
            animation.finish();
            return result;
        }"""
    )
    assert entrance["name"] == "notice-enter"
    assert entrance["duration"] == 200
    assert 0 < entrance["opacity"] < 1
    assert entrance["transform"] != "matrix(1, 0, 0, 1, 0, 0)"
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
    assert exit_state["duration"] == 140
    assert 0 < exit_state["opacity"] < 1
    page.clock.run_for(140)
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
    page.locator("#secondary-action").click()
    page.get_by_text("草稿已保存，可稍后继续完善。", exact=True).wait_for()
    assert page.locator("#notice").is_visible()
    page.clock.run_for(2999)
    assert page.locator("#notice").is_visible()
    page.clock.run_for(1)
    assert page.locator("#notice").get_attribute("data-phase") == "closing"
    page.clock.run_for(140)
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
    assert page.locator(f".topbar #header-{key}").is_visible()
    assert page.get_by_role("tab", selected=False).get_attribute("tabindex") == "-1"
    for other in titles.keys() - {key}:
        assert page.locator(f"#page-{other}").is_hidden()
        assert page.locator(f"#header-{other}").is_hidden()


@pytest.mark.parametrize("key", ["config", "runs"])
def test_reload_does_not_flash_config_while_script_or_api_is_loading(browser_page, ui_server, key):
    page = browser_page
    ready(page, ui_server)
    page.locator(f'[data-page="{key}"]').click()
    scripts, requests = [], []
    page.route("**/static/app.js", lambda route: scripts.append(route))
    page.route("**/api/info", lambda route: requests.append(route))
    page.reload(wait_until="commit")
    page.get_by_role("tablist").wait_for()
    assert page.locator("#page-config").is_hidden()
    assert page.locator("#header-config").is_hidden()
    assert page.locator("#header-runs").is_hidden()
    assert page.locator("[data-page].active").count() == 0
    assert page.get_by_role("tab", selected=True).count() == 0
    assert scripts
    with page.expect_request("**/api/info"):
        scripts[0].continue_()
    assert_current_page(page, key)
    assert requests
    assert page.locator("#input-machines-0-ssh-host").count() == 0
    requests[0].continue_()
    page.wait_for_function("() => document.querySelector('#input-machines-0-ssh-host') !== null")
    assert_current_page(page, key)


@pytest.mark.parametrize("key", ["config", "runs"])
def test_tab_page_survives_reload_without_selected_run(browser_page, ui_server, key):
    page = browser_page
    ready(page, ui_server)
    page.locator(f'[data-page="{key}"]').click()
    page.reload()
    assert_current_page(page, key)
    if key == "runs":
        page.get_by_text("没有匹配的运行记录", exact=True).wait_for()
        assert page.locator("#state-location").text_content() == str(
            ui_server.config.policy.state_dir
        )
    assert page.evaluate("sessionStorage.getItem('autobench-selection')") is None


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
    assert page.locator("#state-location").text_content() == str(history)
    page.get_by_role("button", name="当前查看", exact=True).wait_for()
    assert page.get_by_role("button", name="当前查看", exact=True).count() == 1


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


def test_readable_run_names_and_nested_downloads(browser_page, ui_server, monkeypatch):
    from autobench.storage import Store

    page, server = browser_page, ui_server
    name = "2026-11-03_14-30-25"
    with monkeypatch.context() as patch:
        patch.setattr("autobench.storage.time.strftime", lambda *args: name)
        store = Store(server.config.policy.state_dir)
        store.create(server.config)
        run_id = store.create(server.config)
        store.close()
    relative = "server01/os1/ub/round-001/run.stdout"
    result = server.config.policy.output_dir / run_id / relative
    result.parent.mkdir(parents=True)
    result.write_text("readable archive result\n")
    ready(page, server)
    page.locator('[data-page="runs"]').click()
    page.locator("#runs-list").get_by_text(name, exact=True).wait_for()
    row = page.locator("#runs-list tr").filter(has_text=run_id)
    row.get_by_text(run_id, exact=True).wait_for()
    row.get_by_role("button", name="查看详情", exact=True).click()
    page.locator("#run-detail").get_by_text(run_id, exact=True).wait_for()
    artifact = page.locator(".artifact-row").filter(has_text="run.stdout")
    artifact.get_by_role("button", name="预览", exact=True).click()
    page.locator("#preview-path").get_by_text(relative, exact=True).wait_for()
    assert page.locator("#artifact-preview").text_content() == result.read_text()
    page.locator("#close-preview").click()
    with page.expect_download() as downloaded_file:
        artifact.get_by_role("link", name="下载", exact=True).click()
    assert downloaded_file.value.suggested_filename == "run.stdout"
    assert Path(downloaded_file.value.path()).read_text() == result.read_text()
    with page.expect_download() as downloaded_archive:
        page.get_by_role("link", name="下载完整归档", exact=True).click()
    assert downloaded_archive.value.suggested_filename == f"autobench-{run_id}.zip"
    with zipfile.ZipFile(downloaded_archive.value.path()) as zipped:
        assert zipped.read(f"{run_id}/{relative}") == result.read_bytes()


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
    assert page.locator("#use-config-location").evaluate(
        "element => element === document.activeElement"
    )
    page.keyboard.press("Tab")
    assert page.locator("#change-location").evaluate(
        "element => element === document.activeElement"
    )
    page.keyboard.press("Tab")
    assert page.locator("#page-runs").evaluate("element => element === document.activeElement")


@pytest.mark.parametrize("width", [1920, 1440, 1200, 1101, 1100, 768, 700, 640, 390])
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
    page.locator(".wizard-step").nth(3).click()
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
                .getPropertyValue('--topbar-height')) -
                document.querySelector('.topbar').getBoundingClientRect().height) < 1"""
        )
        header = page.locator(f"#header-{key}").bounding_box()
        tabs = page.locator(".page-tabs").bounding_box()
        assert header["x"] >= tabs["x"] + tabs["width"]
        assert page.locator(".topbar").bounding_box()["height"] == 44
        selectors = [".brand", "#tab-config", "#tab-runs", f"#header-{key}", ".service-info"]
        selectors += (
            [
                "#file-name", "#save-state", "#mode-toggle", "#file-menu summary",
                "#file-location", "#matrix-summary",
            ]
            if key == "config"
            else ["#state-location", "#use-config-location", "#change-location"]
        )
        for selector in selectors:
            element = page.locator(selector)
            if element.is_visible():
                box = element.bounding_box()
                assert abs(box["y"] + box["height"] / 2 - 22) <= 1, (selector, box)
        assert page.locator(".service-info").is_visible() == (width > 1200)
        if key == "config":
            assert page.locator("#matrix-summary").is_visible() == (width > 1600)
            toggle = page.locator("#mode-toggle").bounding_box()
            menu = page.locator("#file-menu summary").bounding_box()
            assert toggle["x"] + toggle["width"] <= menu["x"]
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate("window.scrollTo(0, 400)")
        assert page.locator(".topbar").bounding_box()["y"] == 0
    page.locator("#tab-config").click()
    assert "运行设置" in page.locator('.wizard-step[aria-current="step"]').inner_text()
    page.locator(".wizard-step").nth(0).click()
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
    toggle = page.get_by_role("switch", name="YAML 源码模式", exact=True)
    assert page.locator(".topbar #mode-toggle").is_visible()
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
    toggle.focus()
    page.keyboard.press("Enter")
    page.locator("#config-layout").wait_for()
    assert toggle.get_attribute("aria-checked") == "false"
    assert "运行设置" in page.locator('.wizard-step[aria-current="step"]').inner_text()
    set_editor_mode(page, "yaml")
    assert page.locator("#yaml-editor").input_value() == text
    assert page.locator(".topbar").bounding_box()["height"] == 44
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_header_mode_switch_waits_for_parse_before_updating(browser_page, ui_server):
    page = browser_page
    ready(page, ui_server)
    set_editor_mode(page, "yaml")
    pending = []
    page.route("**/api/config/parse", lambda route: pending.append(route))
    toggle = page.get_by_role("switch", name="YAML 源码模式", exact=True)
    toggle.click()
    page.wait_for_function("() => document.querySelector('#mode-toggle').disabled")
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
    ready(page, ui_server)
    page.locator("#input-machines-0-ssh-host").fill("unsaved.example")
    page.locator(".wizard-step").nth(3).click()
    page.get_by_role("tab", name="运行记录", exact=True).click()
    assert_current_page(page, "runs")
    page.get_by_role("tab", name="测试配置", exact=True).click()
    assert page.locator('.wizard-step[aria-current="step"]').count() == 1
    assert "运行设置" in page.locator('.wizard-step[aria-current="step"]').inner_text()
    page.locator(".wizard-step").nth(0).click()
    assert page.locator("#input-machines-0-ssh-host").input_value() == "unsaved.example"
    set_editor_mode(page, "yaml")
    text = page.locator("#yaml-editor").input_value() + "\n# unsaved tab draft\n"
    page.locator("#yaml-editor").fill(text)
    page.get_by_role("tab", name="运行记录", exact=True).click()
    page.get_by_role("tab", name="测试配置", exact=True).click()
    assert page.locator("#yaml-editor").is_visible()
    assert page.locator("#yaml-editor").input_value() == text
    assert page.locator("#save-state").text_content() == "未保存"
    assert not (ui_server.root / "autobench.yaml").exists()
    assert page.locator(".brand").is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_wizard_password_connection_test_manual_system_files_save_run_and_cancel(
    browser_page, ui_server
):
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
    page.get_by_role("button", name="连接测试", exact=True).click()
    page.locator('.connection-test-status[data-phase="success"]').wait_for()
    page.locator("#primary-action").click()
    page.get_by_role("button", name="＋ 手动添加系统", exact=True).click()
    page.locator("#input-machines-0-systems-0-boot_entry").fill("0001")
    page.locator("#primary-action").click()
    page.locator('[data-field-path=\'["benchmarks",0,"source_archive"]\']').get_by_role(
        "button", name="选择…"
    ).click()
    page.get_by_role("button", name="▤ source.tar.gz 文件").click()
    page.locator("#file-confirm").click()
    page.locator("#primary-action").click()
    # The fourth step contains the full scheduling controls.
    assert page.locator("#wizard-steps [aria-current=step]").text_content() == "4运行设置"
    page.get_by_text("高级调度设置", exact=True).click()
    page.locator("#input-policy-state_dir").fill(str(server.config.policy.state_dir))
    page.locator("#input-policy-poll_interval").fill("0.02")
    page.locator("#input-policy-settle_seconds").fill("0")
    page.locator("#primary-action").click()
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
    assert saved["machines"][0]["systems"][0]["boot_entry"] == "0001"
    page.locator("#primary-action").click()
    page.get_by_role("button", name="取消运行", exact=True).wait_for()
    assert_current_page(page, "runs")
    page.reload()
    assert_current_page(page, "runs")
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
    assert not set(page.locator(".artifact-name strong").all_text_contents()) & REMOVED_OUTPUTS
    with zipfile.ZipFile(info.value.path()) as zipped:
        assert not {PurePosixPath(name).name for name in zipped.namelist()} & REMOVED_OUTPUTS
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
    set_editor_mode(page, "yaml")
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
    open_step(page, "检查运行")
    page.locator("#primary-action").click()
    page.locator(".error-link").filter(has_text="future_data").click()
    page.locator("#yaml-panel").wait_for()
    assert yaml.safe_load(page.locator("#yaml-editor").input_value()) == expected


def test_connection_test_spinner_success_and_configuration_invalidation(browser_page, ui_server):
    page, server = browser_page, ui_server
    ready(page, server)
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
    assert status.evaluate("e => getComputedStyle(e).color") == "rgb(32, 101, 76)"
    assert not connect.is_disabled()
    assert page.locator(".connection-test-panel button").count() == 1
    assert page.locator(".connection-test-panel > *").count() == 2
    page.locator("#input-machines-0-ssh-host").fill("different-host")
    page.locator('.connection-test-status[data-phase="stale"]').wait_for()
    assert page.locator(".status-icon").count() == 0
    page.locator("#input-machines-0-ssh-host").fill("localhost")
    assert page.locator('.connection-test-status[data-phase="stale"]').count() == 1
    set_editor_mode(page, "yaml")
    page.locator("#yaml-panel").wait_for()
    assert (
        yaml.safe_load(page.locator("#yaml-editor").input_value())["machines"][0]["systems"] == []
    )
    assert not (server.root / "autobench.yaml").exists()


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
    connect.click()
    status = page.locator('.connection-test-status[data-phase="error"]')
    status.wait_for()
    assert status.locator(".status-icon svg[aria-hidden=true]").count() == 1
    assert status.evaluate("e => getComputedStyle(e).color") == "rgb(165, 62, 53)"
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
    assert status_box["x"] > button_box["x"]
    assert abs(status_box["y"] - button_box["y"]) < 1
    assert status_box["y"] < button_box["y"] + button_box["height"]
    assert button_box["y"] < status_box["y"] + status_box["height"]
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    connect.click()
    page.locator('.connection-test-status[data-phase="running"]').wait_for()
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
    page.locator('.connection-test-status[data-phase="success"]').wait_for()
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
    assert selected.text_content() == "custom-test×"
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
    page.locator("#secondary-action").click()
    page.wait_for_function("() => document.querySelector('#save-state').textContent === '已保存'")
    assert yaml.safe_load(server.existing.read_text())["benchmarks"][0]["tests"] == [
        "execl",
        "shell",
        "hanoi",
    ]
    page.reload()
    page.locator("#input-machines-0-ssh-host").wait_for()
    open_step(page, "设置测试")
    assert page.get_by_role("checkbox", name="shell · shell1、shell8、shell16").is_checked()
    assert page.get_by_role("list", name="已选测试项").text_content() == "execl×shell×hanoi×"
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
    open_step(page, "检查运行")
    page.clock.install(time=0)
    page.clock.pause_at(1000)
    page.locator("#primary-action").click()
    page.locator("#config-errors").wait_for()
    page.locator(".error-link").first.click()
    assert page.locator("#input-machines-0-ssh-port").get_attribute("aria-invalid") == "true"
    page.clock.run_for(5140)
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
    raw["benchmarks"][0]["repetitions"] = 3
    other = {**raw["benchmarks"][0], "id": "second-benchmark", "repetitions": 2}
    raw["benchmarks"].append(other)
    system_count = sum(len(machine["systems"]) for machine in raw["machines"])
    set_editor_mode(page, "yaml")
    page.locator("#yaml-editor").fill(yaml.safe_dump(raw))
    set_editor_mode(page, "form")
    plan.wait_for(state="visible")
    assert plan.locator(".plan-figure strong").text_content() == str(system_count * 5)
    assert plan.locator(".plan-route li").count() == system_count
    assert plan.locator(".plan-test").count() == len(raw["benchmarks"])
    plan.get_by_role("button", name="编辑测试 →", exact=True).click()
    page.locator("#input-benchmarks-0-repetitions").fill("4")
    assert plan.locator(".plan-figure strong").text_content() == str(system_count * 6)
    set_editor_mode(page, "yaml")
    page.locator("#yaml-panel").wait_for()
    assert plan.is_hidden()
    result = yaml.safe_load(page.locator("#yaml-editor").input_value())
    assert result["benchmarks"][0]["repetitions"] == 4
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
    page.goto(server.url)
    page.get_by_role("button", name="继续处理取消", exact=True).click()
    page.wait_for_function(
        "() => document.querySelector('#run-detail').textContent.includes('运行已取消')"
    )
    assert not server.world.starts


def test_host_key_switch_preserves_values_and_system_override(browser_page, ui_server):
    page, server = browser_page, ui_server
    raw = server.config.model_dump(mode="json")
    raw["machines"][0]["ssh"]["host_key_alias"] = "saved-alias"
    server.existing.write_text(yaml.safe_dump(raw))
    ready(page, server)
    load_existing(page, server)
    page.get_by_text("高级连接设置", exact=True).click()
    original = page.locator("#input-machines-0-ssh-known_hosts").input_value()
    page.get_by_label("忽略主机密钥检查", exact=True).check()
    assert page.locator("#input-machines-0-ssh-known_hosts").count() == 0
    assert page.locator("#input-machines-0-ssh-host_key_alias").count() == 0
    assert page.get_by_text("已忽略主机密钥检查", exact=True).count() == 1
    page.get_by_label("忽略主机密钥检查", exact=True).uncheck()
    assert page.locator("#input-machines-0-ssh-known_hosts").input_value() == original
    assert page.locator("#input-machines-0-ssh-host_key_alias").input_value() == "saved-alias"
    page.get_by_label("忽略主机密钥检查", exact=True).check()
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
    page.locator("#input-machines-0-ssh-host").fill("localhost")
    page.locator("#input-machines-0-ssh-username").fill("bench")
    page.locator("#input-machines-0-ssh-password").fill("test-secret")
    page.get_by_text("高级连接设置", exact=True).click()
    page.locator("#input-machines-0-ssh-known_hosts").fill(str(server.root / "absent-hosts"))
    page.get_by_label("忽略主机密钥检查", exact=True).check()
    page.get_by_role("button", name="连接测试", exact=True).click()
    page.locator('.connection-test-status[data-phase="success"]').wait_for()
    assert not (server.root / "autobench.yaml").exists()
    data = server.config.model_dump(mode="json")
    data["machines"][0]["ssh"].update(verify_host_key=False, known_hosts="absent-hosts")
    set_editor_mode(page, "yaml")
    page.locator("#yaml-panel").wait_for()
    page.locator("#yaml-editor").fill(yaml.safe_dump(data))
    page.locator("#primary-action").click()
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
    assert status.evaluate("e => getComputedStyle(e).color") == "rgb(32, 101, 76)"
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
    assert status.evaluate("e => getComputedStyle(e).color") == "rgb(165, 62, 53)"
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
        assert page.locator("#config-plan").is_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.locator("#prev-step").is_visible() == (index > 0)
        assert page.locator("#primary-action").text_content().strip() == (
            "下一步 →" if index < 4 else "保存并检查"
        )
        if name == "设置测试":
            assert page.locator("#input-policy-output_dir").count() == 0
        if name == "运行设置":
            page.get_by_text("高级调度设置", exact=True).click()
            assert page.locator("#input-policy-output_dir").is_visible()
            assert page.locator("#input-version").is_visible()
    page.locator("#secondary-action").click()
    page.wait_for_function(
        "() => !document.querySelector('#secondary-action').classList.contains('busy')"
    )
    assert server.existing.read_text() == original
    page.reload()
    page.locator("#input-machines-0-ssh-host").wait_for()
    assert page.locator(".wizard-step[aria-current=step]").text_content() == "1连接机器"
    assert page.locator(".wizard-step").count() == 5


@pytest.mark.parametrize("step", ["连接机器", "选择系统"])
def test_machine_management_in_guided_steps(browser_page, ui_server, step):
    page, server = browser_page, ui_server
    ready(page, server)
    load_existing(page, server)
    open_step(page, step)
    assert page.get_by_role("button", name="上移机器", exact=True).is_disabled()
    assert page.get_by_role("button", name="下移机器", exact=True).is_disabled()
    page.get_by_role("button", name="＋ 添加机器", exact=True).click()
    assert page.locator(".machine-tabs .chip-button.active").text_content() == "server02"
    page.get_by_role("button", name="上移机器", exact=True).click()
    assert page.locator(".machine-tabs .chip-button").all_text_contents() == [
        "server02",
        "server01",
    ]
    assert page.locator(".machine-tabs .chip-button.active").text_content() == "server02"
    page.get_by_role("button", name="下移机器", exact=True).click()
    assert page.locator(".machine-tabs .chip-button").all_text_contents() == [
        "server01",
        "server02",
    ]
    page.get_by_role("button", name="移除此机器", exact=True).click()
    assert page.locator(".machine-tabs .chip-button.active").text_content() == "server01"
    page.get_by_role("button", name="移除此机器", exact=True).click()
    assert page.locator(".machine-tabs .chip-button").count() == 0
    assert page.get_by_role("button", name="移除此机器", exact=True).count() == 0
    page.get_by_role("button", name="＋ 添加机器", exact=True).click()
    assert page.locator(".machine-tabs .chip-button.active").text_content() == "server01"
    page.get_by_role("button", name="＋ 添加机器", exact=True).click()
    set_editor_mode(page, "yaml")
    page.locator("#yaml-panel").wait_for()
    result = yaml.safe_load(page.locator("#yaml-editor").input_value())
    assert [m["id"] for m in result["machines"]] == ["server01", "server02"]
    assert all(m["systems"] == [] for m in result["machines"])


def test_benchmark_management_preserves_selection_and_handles_empty_list(browser_page, ui_server):
    page, server = browser_page, ui_server
    ready(page, server)
    open_step(page, "设置测试")
    page.locator("#input-benchmarks-0-source_archive").fill("original.tar.gz")
    assert page.get_by_role("button", name="上移测试", exact=True).is_disabled()
    page.get_by_role("button", name="＋ 添加测试", exact=True).click()
    page.locator("#input-benchmarks-1-source_archive").fill("second.tar.gz")
    page.get_by_role("button", name="上移测试", exact=True).click()
    assert page.locator("#input-benchmarks-0-source_archive").input_value() == "second.tar.gz"
    page.get_by_role("button", name="下移测试", exact=True).click()
    assert page.locator("#input-benchmarks-1-source_archive").input_value() == "second.tar.gz"
    page.get_by_role("button", name="移除此测试", exact=True).click()
    assert page.locator("#input-benchmarks-0-source_archive").input_value() == "original.tar.gz"
    page.get_by_role("button", name="移除此测试", exact=True).click()
    assert page.get_by_text("尚未添加测试", exact=True).is_visible()
    page.locator("#primary-action").click()
    assert page.get_by_text("请添加至少一项测试。", exact=True).is_visible()
    page.get_by_role("button", name="＋ 添加测试", exact=True).click()
    assert page.locator("#input-benchmarks-0-source_archive").input_value() == ""
    assert page.locator(".machine-tabs .chip-button.active").text_content() == "unixbench01"


@pytest.mark.parametrize("field,invalid", [("policy-poll_interval", "-1"), ("version", "2")])
def test_run_settings_error_navigation_and_validation_invalidation(
    browser_page, ui_server, field, invalid
):
    page, server = browser_page, ui_server
    ready(page, server)
    load_existing(page, server)
    open_step(page, "检查运行")
    page.locator("#primary-action").click()
    page.wait_for_function(
        "() => document.querySelector('#primary-action').textContent.includes('开始测试')"
    )
    open_step(page, "运行设置")
    assert page.locator("#primary-action").text_content() == "下一步 →"
    page.get_by_text("高级调度设置", exact=True).click()
    control = page.locator("#input-" + field)
    original = control.input_value()
    control.fill(invalid)
    assert page.locator("#validation-result").is_hidden()
    page.locator("#primary-action").click()
    assert page.locator("#primary-action").text_content() == "保存并检查"
    page.locator("#primary-action").click()
    page.locator("#config-errors").wait_for()
    page.locator(".error-link").first.click()
    assert page.locator(".wizard-step[aria-current=step]").text_content() == "4运行设置"
    assert control.is_visible()
    assert control.get_attribute("aria-invalid") == "true"
    assert control.evaluate("e => e === document.activeElement")
    control.fill(original)
    page.locator("#primary-action").click()
    page.locator("#primary-action").click()
    page.wait_for_function(
        "() => document.querySelector('#primary-action').textContent.includes('开始测试')"
    )
    assert not server.world.starts


def test_imported_yaml_enters_guided_steps_without_changing_content(browser_page, ui_server):
    page, server = browser_page, ui_server
    ready(page, server)
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
    page.locator("#secondary-action").click()
    page.wait_for_function("() => document.querySelector('#save-state').textContent === '已保存'")
    assert (server.root / "autobench.yaml").read_text() == text


def test_guided_system_order_delete_and_readd(browser_page, ui_server):
    page, server = browser_page, ui_server
    ready(page, server)
    load_existing(page, server)
    open_step(page, "选择系统")
    page.get_by_role("button", name="下移系统 os1", exact=True).click()
    assert page.locator(".system-item .object-title").all_text_contents() == ["os2", "os1"]
    page.get_by_role("button", name="上移系统 os1", exact=True).click()
    assert page.locator(".system-item .object-title").all_text_contents() == ["os1", "os2"]
    for _ in range(2):
        page.locator(".system-item").first.get_by_role("button", name="移除", exact=True).click()
    assert page.get_by_role("heading", name="尚未添加系统", exact=True).is_visible()
    page.get_by_role("button", name="＋ 手动添加系统", exact=True).click()
    page.locator("#input-machines-0-systems-0-boot_entry").fill("00AB")
    page.get_by_role("button", name="← 返回系统列表", exact=True).click()
    assert page.locator(".system-item .object-title").all_text_contents() == ["linux01"]
    set_editor_mode(page, "yaml")
    page.locator("#yaml-panel").wait_for()
    data = yaml.safe_load(page.locator("#yaml-editor").input_value())
    assert data["machines"][0]["systems"] == [{"id": "linux01", "boot_entry": "00AB"}]
