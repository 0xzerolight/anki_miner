"""Static cycle-freedom proof over the Japanese rule graph.

Port of Yomitan ``test/language-transformer-cycles.test.js`` (commit e2ed450):
seed one probe per suffix rule (``?`` + inflected suffix), then repeatedly
apply every condition-compatible suffix rule whose inflected suffix matches,
recording each derived node's provenance. If a newly derived node repeats an
ancestor (same rule object, text, and out-conditions), the rule graph admits an
infinite deinflection loop and the test fails. A faithful port at upstream HEAD
must terminate with no cycle, exactly as upstream.
"""

from __future__ import annotations

from anki_miner.services.deinflection import build_condition_flags, conditions_match
from anki_miner.services.japanese_transforms import CONDITIONS, TRANSFORMS


class _RuleNode:
    """One suffix rule + its transform id (upstream ``RuleNode``)."""

    def __init__(self, group_name: str, rule: dict) -> None:
        self.group_name = group_name
        self.rule = rule


class _DeinflectionNode:
    """A probe text + provenance chain (upstream ``DeinflectionNode``)."""

    def __init__(
        self,
        text: str,
        rule_names: list[str],
        rule_node: _RuleNode | None,
        previous: _DeinflectionNode | None,
    ) -> None:
        self.text = text
        self.rule_names = rule_names
        self.rule_node = rule_node
        self.previous = previous

    def history_includes(self, other: _DeinflectionNode) -> bool:
        node: _DeinflectionNode | None = self
        while node is not None:
            if (
                node.rule_node is other.rule_node
                and node.text == other.text
                and _arrays_equal(node.rule_names, other.rule_names)
            ):
                return True
            node = node.previous
        return False

    def get_history(self) -> list[_DeinflectionNode]:
        results: list[_DeinflectionNode] = []
        node: _DeinflectionNode | None = self
        while node is not None:
            results.insert(0, node)
            node = node.previous
        return results


def _arrays_equal(a: list[str], b: list[str]) -> bool:
    # Upstream ``arraysAreEqual``: same length + every member of ``a`` in ``b``.
    if len(a) != len(b):
        return False
    return all(x in b for x in a)


def test_no_cycles_in_japanese_rule_graph() -> None:
    flags = build_condition_flags(CONDITIONS)

    def flags_from_types(condition_types: list[str]) -> int:
        result = 0
        for condition_type in condition_types:
            result |= flags.get(condition_type, 0)
        return result

    rule_nodes: list[_RuleNode] = []
    for transform in TRANSFORMS:
        for rule in transform["rules"]:
            if rule["type"] == "suffix":
                rule_nodes.append(_RuleNode(str(transform["id"]), rule))

    deinflection_nodes: list[_DeinflectionNode] = [
        _DeinflectionNode(f'?{node.rule["inflected"]}', [], None, None) for node in rule_nodes
    ]

    cycles: list[str] = []
    i = 0
    while i < len(deinflection_nodes):
        current = deinflection_nodes[i]
        i += 1
        text = current.text
        rule_names = current.rule_names
        for rule_node in rule_nodes:
            rule = rule_node.rule
            suffix_in = rule["inflected"]
            suffix_out = rule["deinflected"]
            if (
                not conditions_match(
                    flags_from_types(rule_names),
                    flags_from_types(rule["conditionsIn"]),
                )
                or not text.endswith(suffix_in)
                or (len(text) - len(suffix_in) + len(suffix_out)) <= 0
            ):
                continue

            new_node = _DeinflectionNode(
                text[: len(text) - len(suffix_in)] + suffix_out,
                rule["conditionsOut"],
                rule_node,
                current,
            )

            if current.history_includes(new_node):
                stack = []
                for item in new_node.get_history():
                    if item.rule_node is not None:
                        r = item.rule_node.rule
                        stack.append(
                            f"{item.text} ({item.rule_node.group_name}, "
                            f'{",".join(r["conditionsIn"])}=>'
                            f'{",".join(r["conditionsOut"])}, '
                            f'{r["inflected"]}=>{r["deinflected"]})'
                        )
                    else:
                        stack.append(f"{item.text} (start)")
                cycles.append("Cycle detected:\n  " + "\n  ".join(stack))
                continue

            deinflection_nodes.append(new_node)

    assert not cycles, "\n\n".join(cycles)
