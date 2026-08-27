"""Insert encoding="utf-8" at the call sites the audit flags (PS-184).

FOUR correctness constraints, each learned by getting it wrong first:

 1. Identify a call by its FULL SPAN (lineno, col_offset, end_lineno,
    end_col_offset).  Keying on lineno alone patches every call sharing that
    line, which is how `json.loads(p.read_text())` acquired an `encoding=` of
    its own.  A START offset alone is not unique either: in
    `Path(x).read_text()` the outer call and the inner `Path(x)` share one.

 2. Do the offset arithmetic in BYTES.  ast reports col_offset as a UTF-8 byte
    offset, not a character index.  This repo is full of non-ASCII fixtures, so
    a character-indexed scan drifts left by one position per multi-byte
    character and lands inside a DIFFERENT call.

 3. NEVER round-trip through str.  `read_text()` applies universal-newline
    translation, so CRLF silently becomes LF and `write_text` then persists
    that.  This repo has no .gitattributes, so CRLF is stored literally and the
    rewrite is a real content change: an earlier revision of this tool
    normalised 848 lines across three files for 7 real edits, erasing git blame
    on all three.  A tool whose ticket theme is OS PARITY must not rewrite
    Windows line endings.  Read bytes, patch bytes, write bytes.

 4. Insert after the LAST ARGUMENT, not before the closing paren.  Inserting at
    the paren pulls it onto the keyword's line and invents an indent:

        capture_output=True,
        text=True,
        timeout=120,
     encoding="utf-8")           <- 36 sites looked like this

    Appending after the last argument leaves the call's layout untouched.
"""
import ast, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from encoding_audit import find

ENC = b'encoding="utf-8"'

# A line begins after \r\n, \n, or a lone \r -- the three terminators Python's
# tokenizer counts.  bytes.splitlines() is WRONG here: it also splits on \x0b,
# \x0c and \x1c-\x1e, which ast does not, so line numbers would drift.
_EOL = re.compile(rb"\r\n|\n|\r")


def _line_starts(buf: bytes) -> list[int]:
    starts = [0]
    for m in _EOL.finditer(buf):
        starts.append(m.end())
    return starts


def _end_of_last_argument(node: ast.Call) -> tuple[int, int] | None:
    """(end_lineno, end_col_offset) of whichever argument ends LAST.

    Not simply the last element of args+keywords: `f(*a, b=1)` and multi-line
    calls can order the source spans differently from the AST lists.
    """
    parts = [*node.args, *node.keywords]
    if not parts:
        return None
    best = None
    for p in parts:
        el, ec = getattr(p, "end_lineno", None), getattr(p, "end_col_offset", None)
        if el is None or ec is None:
            return None                    # cannot place it safely; bail out
        if best is None or (el, ec) > best:
            best = (el, ec)
    return best


def patch(path: Path) -> int:
    hits = [h for h in find(path) if "AMBIGUOUS" not in h[5]]
    if not hits:
        return 0

    buf = path.read_bytes()                # BYTES end to end: preserves CRLF
    try:
        tree = ast.parse(buf, filename=str(path))
    except SyntaxError:
        return 0
    starts = _line_starts(buf)
    want = {h[:4] for h in hits}

    edits = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        if (n.lineno, n.col_offset, n.end_lineno, n.end_col_offset) not in want:
            continue
        if any(k.arg == "encoding" for k in n.keywords):
            continue

        last = _end_of_last_argument(n)
        if last is None:
            # No arguments at all: fall back to just inside the closing paren.
            end = starts[n.end_lineno - 1] + n.end_col_offset
            close = buf.rindex(b")", 0, end)
            edits.append((close, ENC))
            continue

        pos = starts[last[0] - 1] + last[1]
        # Step over a trailing comma so `f(a, b,)` becomes `f(a, b, encoding=…,)`
        # rather than `f(a, b, encoding=…,)` with a doubled comma.
        tail = pos
        while tail < len(buf) and buf[tail:tail + 1] in b" \t":
            tail += 1
        if buf[tail:tail + 1] == b",":
            edits.append((tail + 1, b" " + ENC + b","))
        else:
            edits.append((pos, b", " + ENC))

    for pos, ins in sorted(edits, reverse=True):
        buf = buf[:pos] + ins + buf[pos:]

    ast.parse(buf, filename=str(path))     # refuse to write a broken file
    path.write_bytes(buf)                  # byte-exact: line endings survive
    return len(edits)


if __name__ == "__main__":
    total = 0
    for root in sys.argv[1:]:
        for p in sorted(Path(root).rglob("*.py")):
            if "__pycache__" not in p.parts:
                total += patch(p)
    print(f"patched {total} call sites")
