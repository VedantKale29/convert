from uigen.ir import UIDocument
from uigen.validators import coverage, validate


def node(t, text=None, variant=None, props=None, children=None):
    return {
        "type": t,
        "variant": variant,
        "text": text,
        "props": [{"name": k, "value": v} for k, v in (props or {}).items()],
        "children": children or [],
    }


def doc(*children, root_props=None):
    return UIDocument.model_validate(
        {
            "title": "t",
            "unsupported": [],
            "root": node("Stack", props=root_props or {"direction": "column"}, children=list(children)),
        }
    )


def test_valid_document():
    d = doc(node("Heading", "Hi", props={"level": 1}), node("Button", "Go", "primary"))
    assert validate(d) == ([], [])


def test_registry_errors_name_the_node_and_the_fix():
    d = doc(
        node("Button", "", "fancy"),
        node("Input", props={"inputType": "email"}),
        node("Grid", props={"columns": 9}),
        node("Text", "x", children=[node("Text", "y")]),
        node("Heading", "H", props={"level": 1, "size": "big"}),
        root_props={"direction": "column", "gap": "huge"},
    )
    errors, _ = validate(d)
    assert (
        "root.children[0] (Button): variant 'fancy' not allowed, use one of ['primary', 'secondary', 'danger', 'ghost']"
        in errors
    )
    assert "root.children[0] (Button): text is required" in errors
    assert "root.children[1] (Input): missing required prop 'label'" in errors
    assert "root.children[2] (Grid): prop 'columns' must be a whole number 1..6, got 9.0" in errors
    assert "root.children[3] (Text): is a leaf component and cannot have children" in errors
    assert "root.children[4] (Heading): unknown prop 'size', allowed: ['level']" in errors
    assert any("prop 'gap' must be a spacing token" in e for e in errors)


def test_root_must_be_container_and_limits():
    d = UIDocument.model_validate({"title": "t", "unsupported": [], "root": node("Button", "x")})
    assert "root must be a container component (e.g. Stack or Grid)" in validate(d)[0]
    big = doc(*[node("Text", "x") for _ in range(401)])
    assert any("limit is 400" in e for e in validate(big)[0])


def test_accessibility_rules():
    d = doc(node("Image", props={"alt": "image"}))
    errors, warnings = validate(d)
    assert errors == ["root.children[0] (Image): alt text must describe the image"]
    assert warnings == ["no Heading on the screen (screen readers use headings to navigate)"]


def test_coverage_counts_placeholders():
    d = doc(node("Heading", "H", props={"level": 1}), node("Placeholder", "3D model viewer"))
    assert coverage(d) == (0.667, 1)


def test_nested_form_is_rejected_and_duplicates_warned():
    d = doc(
        node("Form", children=[node("Stack", props={"direction": "column"}, children=[node("Form")])]),
        node("Tabs", props={"items": ["A", "B", "A"]}),
    )
    errors, warnings = validate(d)
    assert errors == [
        "root.children[0].children[0].children[0] (Form): a Form cannot be inside another Form "
        "(invalid HTML); use a Stack"
    ]
    assert "root.children[1] (Tabs): duplicate items ['A']" in warnings


def test_active_index_must_point_at_an_item():
    d = doc(
        node("List", variant="tabbar", props={"items": ["Home", "Scan"], "active": 2}),
        node("Tabs", variant="pills", props={"items": ["All", "Late"], "active": 1}),
    )
    errors, _ = validate(d)
    assert errors == ["root.children[0] (List): active is 2 but there are only 2 items (0-based index, 0..1)"]
