"""Score a generated IR against a hand-checked expected IR (structural accuracy)."""

from collections import Counter

from .ir import iter_nodes

TEXT_PROPS = {"label", "value", "placeholder", "alt"}


def _sequence(doc):
    return [(path.count(".children["), node.type) for path, node in iter_nodes(doc.root)]


def _lcs(a, b):
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b):
            cur.append(prev[j] + 1 if x == y else max(prev[j + 1], cur[j]))
        prev = cur
    return prev[-1]


def _f1(match, n_pred, n_gold):
    if not match:
        return 0.0
    p, r = match / n_pred, match / n_gold
    return round(2 * p * r / (p + r), 3)


def texts(doc):
    out = []
    for _, node in iter_nodes(doc.root):
        if node.text:
            out.append(node.text)
        for p in node.props:
            if p.name in TEXT_PROPS and isinstance(p.value, str):
                out.append(p.value)
            elif isinstance(p.value, list):
                for v in p.value:
                    out.extend(v if isinstance(v, list) else [v])
    return [t.strip().lower() for t in out if isinstance(t, str) and t.strip()]


def score(pred, gold):
    ps, gs = _sequence(pred), _sequence(gold)
    pt, gt = Counter(t for _, t in ps), Counter(t for _, t in gs)
    gold_texts, pred_texts = texts(gold), set(texts(pred))
    return {
        "structure_f1": _f1(_lcs(ps, gs), len(ps), len(gs)),  # order + nesting + types
        "type_f1": _f1(sum((pt & gt).values()), len(ps), len(gs)),  # right components, any position
        "text_recall": round(sum(t in pred_texts for t in gold_texts) / len(gold_texts), 3) if gold_texts else None,
        "nodes_pred": len(ps),
        "nodes_gold": len(gs),
    }
