import re
import unittest

from bareum_ai import specs

STEP_MOVES = {"GROUNDS", "PROBLEM", "ASSESSMENT", "EFFECT"}


class SpecLoadTests(unittest.TestCase):
    def test_all_specs_have_version(self):
        for name in specs.SPEC_NAMES:
            with self.subTest(name=name):
                self.assertTrue(specs.load(name)["meta"]["version"])

    def test_unknown_spec_is_rejected(self):
        with self.assertRaises(ValueError):
            specs.load("unknown")


class MovesTests(unittest.TestCase):
    def setUp(self):
        self.moves = specs.load("moves")["moves"]

    def test_steps_only_on_step_moves(self):
        with_steps = {name for name, move in self.moves.items() if "steps" in move}
        self.assertEqual(with_steps, STEP_MOVES)

    def test_rag_only_on_grounds_steps(self):
        for name, move in self.moves.items():
            for step_name, step in move.get("steps", {}).items():
                with self.subTest(move=name, step=step_name):
                    self.assertEqual("rag" in step, name == "GROUNDS")


class SectionsTests(unittest.TestCase):
    def setUp(self):
        self.sections = specs.load("sections")

    def test_outline_tags_are_defined(self):
        tags = set(self.sections["tags"])
        for outline_id, outline in self.sections["outlines"].items():
            for slot in outline["required"] + outline["order"]:
                names = slot["any_of"] if isinstance(slot, dict) else [slot]
                with self.subTest(outline=outline_id, slot=slot):
                    self.assertLessEqual(set(names), tags)

    def test_unordered_tags_are_defined(self):
        self.assertLessEqual(set(self.sections["unordered"]), set(self.sections["tags"]))


class RulesInvariantTests(unittest.TestCase):
    """rules.yaml constraints 중 로드 단계에서 검증하는 불변식(R3·R5·R8·R9·R11)."""

    def setUp(self):
        catalog = specs.load("rules")
        self.rules = catalog["rules"]
        self.severity_policy = catalog["severity_policy"]

    def test_r3_internal_evidence_is_multi_span(self):
        for rule_id, rule in self.rules.items():
            if rule["evidence"]["source"] == "internal":
                with self.subTest(rule=rule_id):
                    self.assertEqual(rule["scope"], "multi_span")

    def test_r5_tier_c_has_no_corrupt_fn(self):
        for rule_id, rule in self.rules.items():
            synth = rule.get("synth", {})
            if synth.get("tier") == "C":
                with self.subTest(rule=rule_id):
                    self.assertNotIn("corrupt_fn", synth)

    def test_r8_model_judgment_has_gate(self):
        for rule_id, rule in self.rules.items():
            if rule["judgment"] == "model":
                with self.subTest(rule=rule_id):
                    self.assertIn("gate", rule.get("threshold", {}))

    def test_r9_suppressed_rules_exist(self):
        for rule_id, rule in self.rules.items():
            for target in rule.get("suppresses", []):
                with self.subTest(rule=rule_id, target=target):
                    self.assertIn(target, self.rules)

    def test_r11_severity_follows_basis(self):
        for rule_id, rule in self.rules.items():
            with self.subTest(rule=rule_id):
                basis = self.severity_policy[rule["severity_basis"]]
                self.assertEqual(rule["default_severity"], basis["severity"])


class CrossReferenceTests(unittest.TestCase):
    """rules.yaml이 참조하는 moves·sections 키가 실제로 있는지 확인한다."""

    def setUp(self):
        catalog = specs.load("rules")
        self.rules = catalog["rules"]
        self.consumable = set(catalog["enums"]["consumes"])
        self.moves = specs.load("moves")["moves"]
        self.sections = specs.load("sections")

    def test_consumes_resolve(self):
        for rule_id, rule in self.rules.items():
            for ref in rule.get("consumes", []):
                with self.subTest(rule=rule_id, ref=ref):
                    self.assertTrue(self.resolves(ref))

    def resolves(self, ref):
        head, _, rest = ref.partition(".")
        if head == "moves":
            move_name, _, attr = rest.partition(".")
            move = self.moves.get(move_name)
            if move is None:
                return False
            return attr in {"", "step", "spec_field"} and (
                attr != "step" or "steps" in move
            ) and (attr != "spec_field" or "field" in move)
        if head == "sections":
            return rest in self.sections["tags"] or rest in {"tag", "outlines"}
        return ref in self.consumable

    def test_threshold_section_paths_exist(self):
        pattern = re.compile(r"sections\.yaml#(\w+)")
        for rule_id, rule in self.rules.items():
            for value in rule.get("threshold", {}).values():
                for key in pattern.findall(str(value)):
                    with self.subTest(rule=rule_id, key=key):
                        self.assertIn(key, self.sections)

    def test_move_consumers_match_rules(self):
        consumed_by = {
            (rule_id, name)
            for name, move in self.moves.items()
            for rule_id in move.get("consumed_by", [])
        }
        consumes = {
            (rule_id, ref.split(".")[1])
            for rule_id, rule in self.rules.items()
            for ref in rule.get("consumes", [])
            if ref.startswith("moves.")
        }
        self.assertEqual(consumed_by, consumes)


if __name__ == "__main__":
    unittest.main()
