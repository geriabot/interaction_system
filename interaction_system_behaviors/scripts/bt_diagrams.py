#!/usr/bin/env python3
# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Draw the Behavior Trees of the dialogue strategies, from their XML, with Graphviz.

    python3 interaction_system_behaviors/scripts/bt_diagrams.py [--out media/bt] [--xml DIR] [--format png]

One SVG per strategy (its main tree, with the subtrees of its own file expanded and the shared
subtrees of common.xml drawn as single boxes), common.svg with all the shared subtrees, and
common/<ID>.svg with each shared subtree alone (the shared subtrees it uses drawn as boxes). Run it
after changing a tree, so that the figures of the documentation stay in sync with the trees. Needs
the `dot` command of Graphviz.

Node shapes: control nodes in grey (Sequence →, Fallback ?, Repeat ↻, IfThenElse if/then/else,
Switch with the value of each branch),
questions to the interaction intelligence (AskIntelligence, the skill) in blue, speech (Speak,
Listen, and WithFillers, which says the conversational fillers while its child prepares the
reply) in purple, conditions in yellow, scripts (blackboard updates) in white, shared subtrees in
green.
"""

import argparse
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
XML_DIR = os.path.normpath(os.path.join(HERE, "..", "behavior_tree_xml"))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))

CONTROL = {"Sequence": "→  Sequence", "Fallback": "?  Fallback", "ReactiveSequence": "→  Reactive",
           "ReactiveFallback": "?  Reactive fallback", "IfThenElse": "if / then / else",
           "Repeat": "↻  Repeat", "RetryUntilSuccessful": "↻  Retry", "Inverter": "¬  Inverter",
           "ForceSuccess": "Force success", "ForceFailure": "Force failure"}
STYLE = {
    "control": 'shape=box, style="rounded,filled", fillcolor="#e5e7eb", color="#6b7280"',
    "llm": 'shape=box, style="rounded,filled", fillcolor="#dbeafe", color="#2563eb"',
    "speech": 'shape=box, style="rounded,filled", fillcolor="#ede9fe", color="#7c3aed"',
    "condition": 'shape=box, style="filled", fillcolor="#fef9c3", color="#ca8a04"',
    "script": 'shape=box, style="filled", fillcolor="#ffffff", color="#9ca3af"',
    "subtree": 'shape=box, style="rounded,filled,bold", fillcolor="#dcfce7", color="#16a34a"',
    "other": 'shape=box, color="#6b7280"',
}


def trees_of(path: str) -> dict:
    """{tree ID: element} of an XML file."""
    return {t.get("ID"): t for t in ET.parse(path).getroot().findall("BehaviorTree")}


def quote(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def label_of(node) -> tuple[str, str]:
    """(label, style) of a node."""
    tag = node.tag
    if tag.startswith("Switch"):
        return "switch  " + node.get("variable", ""), "control"
    if tag == "AlwaysFailure":
        return "fail", "condition"
    if tag in CONTROL:
        label = CONTROL[tag]
        if tag == "Repeat" and node.get("num_cycles") == "-1":
            label += "\n(until a child fails)"
        return label, "control"
    if tag == "AskIntelligence":
        extra = [f"{k}: {v}" for k, v in node.attrib.items() if k in ("user_text", "option", "number", "args")]
        return "\n".join(["ask " + node.get("skill", "?")] + extra), "llm"
    if tag == "WithFillers":
        return "WithFillers\n(a filler if the reply\ntakes > filler_after_s)", "speech"
    if tag in ("Speak", "Listen"):
        port = node.get("text", "")
        return f"{tag}\n{port}" if port else tag, "speech"
    if tag == "ScriptCondition":
        return "if  " + node.get("code", ""), "condition"
    if tag == "Script":
        return node.get("code", "").replace("; ", "\n"), "script"
    return tag, "other"


class Drawer:
    def __init__(self, own: dict, shared: set):
        self.own, self.shared = own, shared
        self.lines, self.count = [], 0

    def node(self, label: str, style: str) -> str:
        self.count += 1
        name = f"n{self.count}"
        self.lines.append(f'  {name} [label="{quote(label)}", {STYLE[style]}];')
        return name

    def edge(self, a: str, b: str, label: str = "") -> None:
        attr = f' [label="{label}", fontsize=10]' if label else ""
        self.lines.append(f"  {a} -> {b}{attr};")

    def draw(self, element, expand_own: bool = True) -> str:
        """Draw an element and its children; returns its node name."""
        if element.tag == "SubTree":
            sid = element.get("ID")
            params = [f"{k} = {v}" for k, v in element.attrib.items() if k not in ("ID", "_autoremap")]
            if expand_own and sid in self.own and sid not in self.shared:
                head = self.node("\n".join([sid] + params), "subtree")
                child = self.draw(list(self.own[sid])[0])
                self.edge(head, child)
                return head
            return self.node(("shared: " if sid in self.shared else "") + sid, "subtree")
        label, style = label_of(element)
        me = self.node(label, style)
        children = list(element)
        if element.tag == "IfThenElse":
            names = ["if", "then", "else"]
        elif element.tag.startswith("Switch"):
            # case_1..case_N, then the default branch
            names = [f"= {element.get(f'case_{i}', '')}" for i in range(1, len(children))]
            names.append("otherwise")
        else:
            names = [""] * len(children)
        for child, name in zip(children, names):
            self.edge(me, self.draw(child, expand_own), name)
        return me


def render(dot: str, path: str) -> None:
    fmt = os.path.splitext(path)[1][1:]
    result = subprocess.run(["dot", f"-T{fmt}", "-o", path], input=dot, text=True,
                            capture_output=True)
    if result.returncode != 0:
        sys.exit(f"dot failed for {path}: {result.stderr}")


def graph(title: str, body: list[str]) -> str:
    return "\n".join([
        "digraph BT {",
        '  graph [rankdir=TB, fontname="Helvetica", labelloc=t, fontsize=16, '
        f'label="{quote(title)}", nodesep=0.25, ranksep=0.35];',
        '  node [fontname="Helvetica", fontsize=11, margin="0.12,0.06"];',
        '  edge [color="#6b7280", arrowsize=0.6];',
        *body, "}"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--xml", default=XML_DIR, help="The folder of the trees")
    parser.add_argument("--out", default=os.path.join(REPO, "media", "bt"),
                        help="Where to write the figures")
    parser.add_argument("--format", choices=("svg", "png"), default="svg")
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)
    common = trees_of(os.path.join(args.xml, "common.xml"))
    shared = set(common)

    for name in sorted(os.listdir(args.xml)):
        if not name.endswith(".xml") or name == "common.xml":
            continue
        path = os.path.join(args.xml, name)
        root = ET.parse(path).getroot()
        main_id = root.get("main_tree_to_execute")
        own = trees_of(path)
        if main_id not in own:
            continue
        drawer = Drawer(own, shared)
        drawer.draw(list(own[main_id])[0])
        out = os.path.join(args.out, name.replace(".xml", "." + args.format))
        render(graph(f"{main_id} (behavior_tree_xml/{name})", drawer.lines), out)
        print(out)

    # The shared subtrees, side by side
    drawer = Drawer(common, set())
    for sid, tree in common.items():
        head = drawer.node(sid, "subtree")
        drawer.edge(head, drawer.draw(list(tree)[0], expand_own=False))
    out = os.path.join(args.out, "common." + args.format)
    render(graph("Shared subtrees (behavior_tree_xml/common.xml)", drawer.lines), out)
    print(out)

    # Each shared subtree alone
    os.makedirs(os.path.join(args.out, "common"), exist_ok=True)
    for sid, tree in common.items():
        drawer = Drawer(common, shared)
        head = drawer.node(sid, "subtree")
        drawer.edge(head, drawer.draw(list(tree)[0], expand_own=False))
        out = os.path.join(args.out, "common", f"{sid}.{args.format}")
        render(graph(f"{sid} (behavior_tree_xml/common.xml)", drawer.lines), out)
        print(out)


if __name__ == "__main__":
    main()
