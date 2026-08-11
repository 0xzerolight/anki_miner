from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.controllers.task_registry import TaskOutcome, TaskRegistry, TaskSpec


def test_reveal_request_is_relayed_only_for_a_running_task(qtbot):
    registry = TaskRegistry()
    requests: list[str] = []
    registry.reveal_requested.connect(requests.append)
    handle = registry.start(
        TaskSpec("resource-download", "Recommended resources", CapabilityTarget("settings", "dictionaries"))
    )

    registry.request_reveal("resource-download")
    handle.finish(TaskOutcome.SUCCEEDED)
    registry.request_reveal("resource-download")

    assert requests == ["resource-download"]
    registry.shutdown()
