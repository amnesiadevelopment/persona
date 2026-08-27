"""Insert encoding="utf-8" at the call sites the audit flags (PS-184).

TWO correctness constraints, both learned the hard way:

 1. Identify a call by (lineno, col_offset) - its EXACT node position.  Keying
    on lineno alone patches every call sharing that line, which is how
    `json.loads(p.read_text())` acquired an `encoding=` of its own.

 2. Do the offset arithmetic in BYTES.  ast reports col_offset as a UTF-8 byte
    offset, not a character index.  This repo is full of non-ASCII fixtures, so
    a character-indexed scan for the closing paren drifts left by one position
    per multi-byte character and lands inside a DIFFERENT call.
"""
import ast, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from encoding_audit import find

ELIGIBLE = {"read_text", "write_text", "open", "run", "check_output",
            "Popen", "call", "check_call"}

def patch(path: Path) -> int:
    hits = [h for h in find(path) if "AMBIGUOUS" not in h[5]]
    if not hits:
        return 0
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    buf = src.encode("utf-8")                      # ALL offsets are byte offsets
    starts, off = [0], 0
    for line in src.splitlines(keepends=True):
        off += len(line.encode("utf-8"))
        starts.append(off)
    want = {h[:4] for h in hits}   # FULL SPAN: a start offset is not unique

    edits = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call) or (n.lineno, n.col_offset,
                                          n.end_lineno, n.end_col_offset) not in want:
            continue
        if any(k.arg == "encoding" for k in n.keywords):
            continue
        end = starts[n.end_lineno - 1] + n.end_col_offset   # just past ')'
        close = buf.rindex(b")", 0, end)
        j = close - 1
        while j >= 0 and buf[j:j+1] in b" \t\r\n":
            j -= 1
        prev = buf[j:j+1]
        ins = b'encoding="utf-8"' if prev == b"(" else (
              b' encoding="utf-8"' if prev == b"," else b', encoding="utf-8"')
        edits.append((close, ins))

    for pos, ins in sorted(edits, reverse=True):
        buf = buf[:pos] + ins + buf[pos:]
    out = buf.decode("utf-8")
    ast.parse(out, filename=str(path))             # refuse to write a broken file
    path.write_text(out, encoding="utf-8")
    return len(edits)

if __name__ == "__main__":
    total = 0
    for root in sys.argv[1:]:
        for p in sorted(Path(root).rglob("*.py")):
            if "__pycache__" not in p.parts:
                total += patch(p)
    print(f"patched {total} call sites")
