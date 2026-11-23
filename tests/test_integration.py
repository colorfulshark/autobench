"""Opt-in tests for real machines. Default pytest never reboots a machine."""

import os
from pathlib import Path

import pytest

from autobench.config import load_config
from autobench.scheduler import Scheduler
from autobench.ssh import RemoteClient
from autobench.storage import Store


@pytest.mark.integration
async def test_real_ssh_systemd_current_os(tmp_path):
    path = os.environ.get("AUTOBENCH_INTEGRATION_CONFIG")
    if not path:
        pytest.skip("set AUTOBENCH_INTEGRATION_CONFIG to a current-OS-only YAML")
    config = load_config(Path(path))
    assert len(config.machines) == 1 and len(config.machines[0].systems) == 1
    machine = config.machines[0]
    system = machine.systems[0]
    client = RemoteClient(machine, system, config.policy)
    probe = await Scheduler.probe_system(client, system)
    assert Scheduler.is_target(probe, system), "this test must not switch OS"
    config.policy.state_dir = tmp_path / "state"
    config.policy.output_dir = tmp_path / "results"
    config.policy.settle_seconds = 0
    config.policy.poll_interval = 1
    store = Store(config.policy.state_dir)
    try:
        run_id = store.create(config)
        assert await Scheduler(store, run_id).execute() == "succeeded"
        assert all(j["details"]["archived"] for j in store.jobs(run_id))
        # The runner is contacted through a fresh SSH connection for every control RPC.
        # A second controller must reconcile without starting a second service.
        assert await Scheduler(store, run_id).execute() == "succeeded"
    finally:
        store.close()


@pytest.mark.uefi
async def test_real_uefi_matrix(tmp_path):
    path = os.environ.get("AUTOBENCH_UEFI_CONFIG")
    if not path:
        pytest.skip("set AUTOBENCH_UEFI_CONFIG to explicitly enable real UEFI reboots")
    config = load_config(Path(path))
    assert any(len(machine.systems) >= 2 for machine in config.machines)
    config.policy.state_dir = tmp_path / "state"
    config.policy.output_dir = tmp_path / "results"
    store = Store(config.policy.state_dir)
    try:
        run_id = store.create(config)
        assert await Scheduler(store, run_id).execute() == "succeeded"
        assert all(j["details"]["archived"] for j in store.jobs(run_id))
        for machine in config.machines:
            final = machine.systems[-1]
            probe = await Scheduler.probe_system(RemoteClient(machine, final, config.policy), final)
            last_job = store.jobs(run_id, machine.id)[-1]
            evidence = last_job["details"].get("grub_boot")
            assert Scheduler.is_target(probe, final, evidence["target"] if evidence else None)
    finally:
        store.close()
