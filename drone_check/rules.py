"""Rule evaluation using CEL (Common Expression Language).

CEL (https://cel.dev) is a non-Turing-complete, sandboxed expression language
standardised by Google and used by Kubernetes, Firebase and Google IAM. Each
rule is a boolean CEL expression evaluated against the snapshot context built by
:meth:`DroneSnapshot.to_cel_context` (bindings ``drone`` and ``checks``).

A rule definition (from ``rules.yaml``) looks like::

    - id: vtx-power-armed-max
      description: "Armed VTX power must not exceed 25 mW"
      severity: critical
      expr: 'drone.vtx.power_armed_max_mw <= 25'
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .model import CheckResult, DroneSnapshot, Evaluation


@dataclass
class Rule:
    id: str
    expr: str
    description: str = ""
    severity: str = "critical"


def load_rules(raw: list[dict]) -> list[Rule]:
    rules: list[Rule] = []
    for item in raw or []:
        rules.append(
            Rule(
                id=str(item["id"]),
                expr=str(item["expr"]),
                description=str(item.get("description", "")),
                severity=str(item.get("severity", "critical")),
            )
        )
    return rules


class RuleEngine:
    """Compiles and evaluates CEL rules.

    The ``celpy`` import is done lazily so that the parser/VTX layers remain
    usable (and testable) without the CEL dependency installed.
    """

    def __init__(self, rules: list[Rule]):
        import celpy  # noqa: F401  (validate availability early)

        self._celpy = celpy
        self._env = celpy.Environment()
        self._programs: dict[str, Any] = {}
        self.rules = rules
        for rule in rules:
            ast = self._env.compile(rule.expr)
            self._programs[rule.id] = self._env.program(ast)

    def evaluate(self, snapshot: DroneSnapshot) -> Evaluation:
        context = snapshot.to_cel_context()
        activation = {k: self._celpy.json_to_cel(v) for k, v in context.items()}

        results: list[CheckResult] = []
        for rule in self.rules:
            passed, detail = self._eval_one(rule, activation)
            results.append(
                CheckResult(
                    rule_id=rule.id,
                    description=rule.description,
                    severity=rule.severity,
                    passed=passed,
                    detail=detail,
                )
            )

        # The drone passes only if every critical rule passes.
        critical_ok = all(r.passed for r in results if r.severity == "critical")
        evaluation = Evaluation(passed=critical_ok, results=results)
        return evaluation

    def _eval_one(self, rule: Rule, activation: dict) -> tuple[bool, str]:
        try:
            value = self._programs[rule.id].evaluate(activation)
        except Exception as exc:
            # A rule that errors (e.g. references a null field) fails closed.
            return False, f"evaluation error: {exc}"
        passed = bool(value)
        detail = "" if passed else f"expression evaluated to {bool(value)}"
        return passed, detail
